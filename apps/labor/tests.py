from datetime import date
from decimal import Decimal

from django.contrib.auth.models import Group
from django.db import IntegrityError, transaction
from django.test import TestCase

from apps.audit.models import AuditEvent
from apps.employees.models import Employee
from apps.organization.models import Company

from . import services
from .models import Contractor, LaborProfile, LaborRate, Trade

D = Decimal


class LaborCase(TestCase):
    def setUp(self):
        self.co = Company.objects.create(code="IV1", name="IV One")
        self.other = Company.objects.create(code="IV2", name="IV Two")
        self.mason = Trade.objects.create(code="MASON", name="Mason")
        self.contractor = Contractor.objects.create(company=self.co, name="Gulf Manpower")

    def new_employee(self, company=None, **kw):
        return Employee(company=company or self.co, first_name=kw.pop("first_name", "Ravi"),
                        joining_date=kw.pop("joining_date", date(2026, 1, 1)), **kw)

    def make_worker(self, engagement="direct", contractor=None, wage_basis="daily", rate=D("5"), **kw):
        return services.create_labor_worker(self.new_employee(), engagement=engagement, contractor=contractor,
                                            trade=self.mason, wage_basis=wage_basis, rate=rate, **kw)


class CreateWorkerTests(LaborCase):
    def test_creates_a_labor_employee_with_a_profile_and_first_rate(self):
        p = self.make_worker()
        self.assertEqual(p.employee.worker_type, "labor")
        self.assertTrue(p.employee.employee_no)                          # numbered automatically
        self.assertEqual(p.company_id, self.co.pk)
        rate = p.current_rate
        self.assertEqual((rate.rate, rate.wage_basis, rate.effective_from), (D("5"), "daily", date(2026, 1, 1)))
        self.assertIsNone(rate.effective_to)

    def test_a_failed_rule_saves_nothing_and_uses_no_number(self):
        with self.assertRaises(services.LaborError):
            self.make_worker(engagement="direct", contractor=self.contractor)
        self.assertEqual(Employee.objects.count(), 0)
        self.assertEqual(LaborProfile.objects.count(), 0)

    def test_contracted_workers_are_paid_daily(self):
        with self.assertRaisesMessage(services.LaborError, "daily rate"):
            self.make_worker(engagement="contracted", wage_basis="monthly", rate=D("300"))
        self.assertEqual(Employee.objects.count(), 0)

    def test_contracted_without_a_contractor_is_allowed(self):
        p = self.make_worker(engagement="contracted")
        self.assertIsNone(p.contractor)

    def test_contractor_must_belong_to_the_workers_company(self):
        foreign = Contractor.objects.create(company=self.other, name="Other Co Supplier")
        with self.assertRaisesMessage(services.LaborError, "another company"):
            self.make_worker(engagement="contracted", contractor=foreign)

    def test_inactive_contractor_is_refused(self):
        self.contractor.is_active = False
        self.contractor.save()
        with self.assertRaisesMessage(services.LaborError, "inactive"):
            self.make_worker(engagement="contracted", contractor=self.contractor)

    def test_rate_and_hours_are_checked(self):
        for kw in ({"rate": D("0")}, {"rate": D("-1")}, {"standard_hours": D("0")}, {"standard_hours": D("25")}):
            with self.assertRaises(services.LaborError, msg=str(kw)):
                self.make_worker(**kw)
        self.assertEqual(Employee.objects.count(), 0)

    def test_database_refuses_a_direct_worker_with_a_contractor(self):
        p = self.make_worker()
        p.contractor = self.contractor
        with self.assertRaises(IntegrityError), transaction.atomic():
            p.save()


class SetupProfileTests(LaborCase):
    def test_gives_an_existing_labor_employee_a_profile(self):
        e = self.new_employee(worker_type="labor")
        e.save()
        self.assertEqual(list(Employee.objects.filter(worker_type="labor", labor_profile__isnull=True)), [e])
        p = services.setup_profile(e, engagement="direct", trade=self.mason, wage_basis="monthly", rate=D("250"))
        self.assertEqual(p.current_rate.effective_from, e.joining_date)
        self.assertFalse(Employee.objects.filter(worker_type="labor", labor_profile__isnull=True).exists())

    def test_staff_cannot_get_a_labor_profile(self):
        e = self.new_employee()
        e.save()
        with self.assertRaisesMessage(services.LaborError, "Only labor workers"):
            services.setup_profile(e, engagement="direct", trade=self.mason, wage_basis="daily", rate=D("5"))

    def test_only_one_profile_per_worker(self):
        p = self.make_worker()
        with self.assertRaisesMessage(services.LaborError, "already has"):
            services.setup_profile(p.employee, engagement="direct", trade=self.mason, wage_basis="daily", rate=D("5"))


class RateHistoryTests(LaborCase):
    def test_new_rate_closes_the_old_one_the_day_before(self):
        p = self.make_worker()
        services.change_rate(p, effective_from=date(2026, 7, 1), wage_basis="daily", rate=D("6"),
                             standard_hours=D("8"), overtime_eligible=True)
        old, new = p.rates.order_by("effective_from")
        self.assertEqual(old.effective_to, date(2026, 6, 30))
        self.assertIsNone(new.effective_to)
        self.assertEqual(p.rates.filter(effective_to__isnull=True).count(), 1)

    def test_rate_on_a_date_gives_the_terms_that_applied_then(self):
        p = self.make_worker()
        services.change_rate(p, effective_from=date(2026, 7, 1), wage_basis="daily", rate=D("6"),
                             standard_hours=D("8"), overtime_eligible=True)
        self.assertEqual(services.rate_on(p, date(2026, 6, 30)).rate, D("5"))
        self.assertEqual(services.rate_on(p, date(2026, 7, 1)).rate, D("6"))
        self.assertEqual(services.rate_on(p, date(2026, 12, 31)).rate, D("6"))
        self.assertIsNone(services.rate_on(p, date(2025, 12, 31)))          # before they joined

    def test_new_rate_must_start_after_the_current_one(self):
        p = self.make_worker()
        for day in (date(2026, 1, 1), date(2025, 12, 1)):
            with self.assertRaisesMessage(services.LaborError, "must start after"):
                services.change_rate(p, effective_from=day, wage_basis="daily", rate=D("6"),
                                     standard_hours=D("8"), overtime_eligible=True)
        self.assertEqual(p.rates.count(), 1)

    def test_contracted_worker_cannot_move_to_a_monthly_salary(self):
        p = self.make_worker(engagement="contracted")
        with self.assertRaisesMessage(services.LaborError, "daily rate"):
            services.change_rate(p, effective_from=date(2026, 7, 1), wage_basis="monthly", rate=D("300"),
                                 standard_hours=D("8"), overtime_eligible=True)

    def test_a_worker_who_left_cannot_be_changed(self):
        p = self.make_worker()
        Employee.objects.filter(pk=p.employee_id).update(status="separated")
        with self.assertRaisesMessage(services.LaborError, "has left"):
            services.change_rate(p, effective_from=date(2026, 7, 1), wage_basis="daily", rate=D("6"),
                                 standard_hours=D("8"), overtime_eligible=True)
        with self.assertRaisesMessage(services.LaborError, "has left"):
            services.update_profile(p, engagement="direct", contractor=None, trade=self.mason)

    def test_database_allows_only_one_open_rate(self):
        p = self.make_worker()
        with self.assertRaises(IntegrityError), transaction.atomic():
            LaborRate.objects.create(profile=p, rate=D("7"), effective_from=date(2026, 8, 1))


class UpdateProfileTests(LaborCase):
    def test_changes_trade_and_contractor(self):
        p = self.make_worker(engagement="contracted")
        electrician = Trade.objects.create(code="ELEC", name="Electrician")
        services.update_profile(p, engagement="contracted", contractor=self.contractor, trade=electrician, notes="n")
        p.refresh_from_db()
        self.assertEqual((p.trade, p.contractor, p.notes), (electrician, self.contractor, "n"))

    def test_switching_to_contracted_needs_a_daily_rate_first(self):
        p = self.make_worker(wage_basis="monthly", rate=D("250"))
        with self.assertRaisesMessage(services.LaborError, "daily rate first"):
            services.update_profile(p, engagement="contracted", contractor=None, trade=self.mason)

    def test_switching_to_direct_needs_the_contractor_cleared(self):
        p = self.make_worker(engagement="contracted", contractor=self.contractor)
        with self.assertRaisesMessage(services.LaborError, "no contractor"):
            services.update_profile(p, engagement="direct", contractor=self.contractor, trade=self.mason)
        services.update_profile(p, engagement="direct", contractor=None, trade=self.mason)
        p.refresh_from_db()
        self.assertEqual((p.engagement, p.contractor), ("direct", None))


class ContractorTests(LaborCase):
    def test_cannot_deactivate_while_workers_are_still_supplied(self):
        p = self.make_worker(engagement="contracted", contractor=self.contractor)
        with self.assertRaisesMessage(services.LaborError, "still supplied"):
            services.set_contractor_active(self.contractor, False)
        Employee.objects.filter(pk=p.employee_id).update(status="separated")      # once they have left it is fine
        services.set_contractor_active(self.contractor, False)
        self.contractor.refresh_from_db()
        self.assertFalse(self.contractor.is_active)

    def test_name_is_unique_per_company_ignoring_case(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Contractor.objects.create(company=self.co, name="gulf manpower")
        Contractor.objects.create(company=self.other, name="Gulf Manpower")          # another company may reuse it

    def test_trade_names_are_unique_ignoring_case(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Trade.objects.create(code="M2", name="MASON")


class DefaultsTests(TestCase):
    def perms(self, group):
        return set(Group.objects.get(name=group).permissions.filter(content_type__app_label="labor")
                   .values_list("codename", flat=True))

    def test_standard_groups_get_labor_permissions_after_migrate(self):
        hr, fin, mgmt = self.perms("HR"), self.perms("Finance"), self.perms("Management")
        self.assertLessEqual({"add_laborprofile", "change_laborprofile", "add_laborrate", "view_laborrate",
                              "add_contractor", "change_trade"}, hr)
        self.assertNotIn("change_laborrate", hr)             # pay terms are replaced by a new row, never edited
        self.assertEqual({c.split("_")[0] for c in fin}, {"view"})
        self.assertIn("view_laborrate", fin)
        self.assertIn("view_laborrate", mgmt)
        # Management also sets the overtime rules and decides overtime claims; nothing else beyond viewing
        self.assertEqual({c for c in mgmt if not c.startswith("view_")}, {"add_overtimepolicy", "change_overtimeclaim"})

    def test_running_it_again_keeps_a_permission_removed_in_the_admin(self):
        from django.contrib.auth.models import Permission
        from apps.labor.defaults import ensure_system_defaults
        hr = Group.objects.get(name="HR")
        hr.permissions.remove(Permission.objects.get(codename="change_trade", content_type__app_label="labor"))
        ensure_system_defaults()
        self.assertNotIn("change_trade", self.perms("HR"))
        self.assertIn("view_trade", self.perms("HR"))

    def test_a_group_emptied_of_a_models_permissions_gets_them_back(self):
        from apps.labor.defaults import ensure_system_defaults
        finance = Group.objects.get(name="Finance")
        finance.permissions.remove(*finance.permissions.filter(content_type__app_label="labor"))
        ensure_system_defaults()
        self.assertIn("view_laborrate", self.perms("Finance"))


class AuditTests(LaborCase):
    def test_everything_about_a_worker_is_logged_against_that_worker(self):
        p = self.make_worker()
        services.change_rate(p, effective_from=date(2026, 7, 1), wage_basis="daily", rate=D("6"),
                             standard_hours=D("8"), overtime_eligible=True)
        events = AuditEvent.objects.filter(module="labor", subject_employee_id=p.employee_id)
        self.assertEqual(events.filter(action="create").count(), 3)      # the profile and two pay rows
        self.assertEqual(events.filter(action="update").count(), 1)      # the old pay row was closed
        self.assertTrue(all(e.company_id == self.co.pk for e in events))

    def test_contractor_and_trade_changes_are_logged(self):
        self.contractor.phone = "1777"
        self.contractor.save()
        self.assertTrue(AuditEvent.objects.filter(module="labor", action="update", object_id=str(self.contractor.pk),
                                                  company_id=self.co.pk).exists())
        self.assertTrue(AuditEvent.objects.filter(module="labor", action="create",
                                                  company_id__isnull=True).exists())        # the trade has no company
