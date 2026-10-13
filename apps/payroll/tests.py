from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import IntegrityError, transaction
from django.test import TestCase

from apps.audit.models import AuditEvent
from apps.configuration import registry
from apps.employees.models import Employee
from apps.organization.models import Company, CompanyAccess

from . import services
from .models import AllowanceType, PaymentDetail, SalaryAllowance, SalaryRecord
from .services import PayrollError

D = Decimal
User = get_user_model()
VALID_IBAN = "BH67BMAG00001299123456"          # the example IBAN used in Bahrain's own documentation


def make_profile(employee, engagement):
    """A labor profile, whatever else the real LaborProfile insists on (the trade, in the real project)."""
    from apps.labor.models import LaborProfile

    kwargs = {"employee": employee, "engagement": engagement}
    if any(f.name == "trade" for f in LaborProfile._meta.get_fields()):
        from apps.labor.models import Trade
        kwargs["trade"] = Trade.objects.get_or_create(code="PAYTEST", defaults={"name": "Payroll test trade"})[0]
    return LaborProfile.objects.create(**kwargs)


class PayrollCase(TestCase):
    def setUp(self):
        self.co = Company.objects.create(code="IV1", name="IV One")
        self.other = Company.objects.create(code="IV2", name="IV Two")
        self.emp = self.person("Sara", "S-1")
        self.housing = AllowanceType.objects.create(company=self.co, name="Housing")
        self.transport = AllowanceType.objects.create(company=self.co, name="Transport", counts_for_sio=True)

    def person(self, first, no, company=None, **kw):
        kw.setdefault("joining_date", date(2020, 1, 1))
        return Employee.objects.create(company=company or self.co, employee_no=no, first_name=first, **kw)

    def pay(self, emp=None, start=date(2024, 1, 1), basic="500", allowances=(), reason="joining", **kw):
        return services.change_salary(emp or self.emp, effective_from=start, basic=D(basic),
                                      allowances=list(allowances), reason=reason, **kw)


class SalaryTests(PayrollCase):
    def test_first_salary_totals_basic_and_allowances(self):
        r = self.pay(allowances=[(self.housing, D("150")), (self.transport, D("50.500"))])
        r = SalaryRecord.objects.get(pk=r.pk)
        self.assertEqual((r.basic, r.allowance_total, r.total), (D("500"), D("200.500"), D("700.500")))
        self.assertEqual((r.company_id, r.effective_to), (self.co.pk, None))

    def test_a_new_salary_ends_the_old_one_the_day_before(self):
        first = self.pay()
        self.pay(start=date(2025, 7, 1), basic="600", reason="increase")
        first.refresh_from_db()
        self.assertEqual(first.effective_to, date(2025, 6, 30))
        self.assertEqual(SalaryRecord.objects.filter(employee=self.emp, effective_to__isnull=True).count(), 1)

    def test_salary_on_follows_the_dates_and_a_future_change_waits(self):
        self.pay()
        self.pay(start=date(2025, 7, 1), basic="600", reason="increase")
        self.assertEqual(services.salary_on(self.emp, date(2025, 6, 30)).basic, D("500"))
        self.assertEqual(services.salary_on(self.emp, date(2025, 7, 1)).basic, D("600"))
        self.assertIsNone(services.salary_on(self.emp, date(2023, 12, 31)))
        self.assertEqual(services.upcoming_salary(self.emp, date(2025, 6, 1)).basic, D("600"))
        self.assertIsNone(services.upcoming_salary(self.emp, date(2025, 8, 1)))

    def test_the_rules(self):
        self.pay(start=date(2024, 1, 1))
        with self.assertRaisesMessage(PayrollError, "joining date"):
            self.pay(self.person("Early", "S-2", joining_date=date(2024, 5, 1)), start=date(2024, 4, 30))
        with self.assertRaisesMessage(PayrollError, "must start after the current one"):
            self.pay(start=date(2024, 1, 1), basic="600")
        with self.assertRaisesMessage(PayrollError, "must start after the current one"):
            self.pay(start=date(2023, 6, 1), basic="600")
        with self.assertRaisesMessage(PayrollError, "more than zero"):
            self.pay(start=date(2025, 1, 1), basic="0")
        with self.assertRaisesMessage(PayrollError, "three decimal places"):
            self.pay(start=date(2025, 1, 1), basic="500.1234")
        with self.assertRaisesMessage(PayrollError, "Choose a reason"):
            self.pay(start=date(2025, 1, 1), reason="whim")
        self.assertEqual(SalaryRecord.objects.filter(employee=self.emp).count(), 1)     # nothing half-saved

    def test_labor_and_people_who_left_have_no_salary_record(self):
        labor = self.person("Ravi", "L-1", worker_type="labor")
        with self.assertRaisesMessage(PayrollError, "Labor workers are paid from their pay terms"):
            self.pay(labor)
        gone = self.person("Gone", "S-3", status="separated")
        with self.assertRaisesMessage(PayrollError, "has left the company"):
            self.pay(gone)

    def test_allowance_rules(self):
        foreign = AllowanceType.objects.create(company=self.other, name="Housing")
        with self.assertRaisesMessage(PayrollError, "belongs to another company"):
            self.pay(allowances=[(foreign, D("10"))])
        off = AllowanceType.objects.create(company=self.co, name="Old one", is_active=False)
        with self.assertRaisesMessage(PayrollError, "switched off"):
            self.pay(allowances=[(off, D("10"))])
        with self.assertRaisesMessage(PayrollError, "more than zero"):
            self.pay(allowances=[(self.housing, D("0"))])
        self.assertFalse(SalaryRecord.objects.exists())
        r = self.pay(allowances=[(self.housing, None), (self.transport, "")])               # blank = not paid
        self.assertEqual(r.allowances.count(), 0)

    def test_sio_wage_counts_only_the_flagged_allowances(self):
        r = self.pay(allowances=[(self.housing, D("150")), (self.transport, D("50"))])
        self.assertEqual(SalaryRecord.objects.get(pk=r.pk).sio_wage, D("550"))


class CorrectionTests(PayrollCase):
    def test_a_correction_changes_the_latest_record_in_place(self):
        r = self.pay(allowances=[(self.housing, D("150")), (self.transport, D("50"))])
        services.correct_salary(r, basic=D("550"), allowances=[(self.housing, D("160")), (self.transport, None)],
                                remarks="Typed 500 instead of 550")
        r = SalaryRecord.objects.get(pk=r.pk)
        self.assertEqual((r.basic, r.remarks), (D("550"), "Typed 500 instead of 550"))
        self.assertEqual({a.allowance_type.name: a.amount for a in r.allowances.all()}, {"Housing": D("160")})
        self.assertEqual(SalaryRecord.objects.count(), 1)

    def test_a_correction_can_add_an_allowance(self):
        r = self.pay()
        services.correct_salary(r, basic=D("500"), allowances=[(self.housing, D("90"))], remarks="Forgot housing")
        self.assertEqual(SalaryAllowance.objects.get().amount, D("90"))

    def test_only_the_latest_can_be_corrected_and_a_reason_is_needed(self):
        old = self.pay()
        self.pay(start=date(2025, 1, 1), basic="600", reason="increase")
        with self.assertRaisesMessage(PayrollError, "Only the latest salary"):
            services.correct_salary(old, basic=D("510"), allowances=[], remarks="x")
        latest = SalaryRecord.objects.get(employee=self.emp, effective_to__isnull=True)
        with self.assertRaisesMessage(PayrollError, "Say what was wrong"):
            services.correct_salary(latest, basic=D("610"), allowances=[], remarks="  ")
        latest.refresh_from_db()
        self.assertEqual(latest.basic, D("600"))


class AuditTests(PayrollCase):
    def test_amounts_and_ibans_are_hidden_in_the_audit_log(self):
        r = self.pay(allowances=[(self.housing, D("150"))])
        services.correct_salary(r, basic=D("550"), allowances=[(self.housing, D("160"))], remarks="fix")
        services.save_payment_details(self.emp, method="bank", bank_name="NBB", iban=VALID_IBAN)
        events = AuditEvent.objects.filter(module="payroll")
        self.assertTrue(events.exists())
        everything = str([e.changes for e in events])
        for secret in ("550", "160", "150", VALID_IBAN, "500"):
            self.assertNotIn(f"'{secret}'", everything)
            self.assertNotIn(f"'{secret}.000'", everything)
        record_event = events.filter(content_type__app_label="payroll", content_type__model="salaryrecord", action="update").get()
        self.assertEqual(record_event.changes["basic"], {"masked": True})
        self.assertEqual(record_event.subject_employee_id, self.emp.pk)
        self.assertEqual(record_event.company_id, self.co.pk)
        self.assertEqual(events.filter(content_type__app_label="payroll", content_type__model="paymentdetail").get().changes["iban"], {"masked": True})
        allowance_event = events.filter(content_type__app_label="payroll", content_type__model="salaryallowance", action="update").get()
        self.assertEqual((allowance_event.changes["amount"], allowance_event.subject_employee_id),
                         ({"masked": True}, self.emp.pk))


class AllowanceTypeTests(PayrollCase):
    def test_names_are_unique_per_company_ignoring_case_and_spaces(self):
        with self.assertRaisesMessage(PayrollError, "already has an allowance called housing"):
            services.create_allowance_type(self.co, name="  housing ")
        other = services.create_allowance_type(self.other, name="Housing")                  # another company may reuse it
        self.assertEqual(other.company, self.other)
        with self.assertRaises(IntegrityError), transaction.atomic():                       # and the database agrees
            AllowanceType.objects.create(company=self.co, name="HOUSING")
        with self.assertRaisesMessage(PayrollError, "Enter a name"):
            services.create_allowance_type(self.co, name="   ")

    def test_rename_and_the_sio_flag(self):
        services.update_allowance_type(self.housing, name="Housing allowance", counts_for_sio=True)
        self.housing.refresh_from_db()
        self.assertEqual((self.housing.name, self.housing.counts_for_sio), ("Housing allowance", True))
        with self.assertRaisesMessage(PayrollError, "already has an allowance"):
            services.update_allowance_type(self.housing, name="transport", counts_for_sio=False)

    def test_an_allowance_in_use_cannot_be_switched_off_until_nobody_has_it(self):
        self.pay(allowances=[(self.housing, D("150"))])
        self.assertEqual(services.people_receiving(self.housing), 1)
        with self.assertRaisesMessage(PayrollError, "1 person is paid Housing"):
            services.set_allowance_active(self.housing, False)
        self.pay(start=date(2025, 1, 1), basic="600", reason="increase")                    # housing dropped
        self.assertEqual(services.people_receiving(self.housing), 0)
        services.set_allowance_active(self.housing, False)
        self.housing.refresh_from_db()
        self.assertFalse(self.housing.is_active)
        services.set_allowance_active(self.housing, True)


class PaymentTests(PayrollCase):
    def test_a_bank_transfer_is_normalised_and_cash_clears_the_bank(self):
        d = services.save_payment_details(self.emp, method="bank", bank_name="  National   Bank ",
                                          iban="bh67 bmag 0000 1299 1234 56")
        d = PaymentDetail.objects.get(pk=d.pk)
        self.assertEqual((d.bank_name, d.iban, d.company_id), ("National Bank", VALID_IBAN, self.co.pk))
        self.assertEqual(d.iban_spaced, "BH67 BMAG 0000 1299 1234 56")
        services.save_payment_details(self.emp, method="cash", bank_name="x", iban="y")
        d = PaymentDetail.objects.get(employee=self.emp)                                    # same row, updated
        self.assertEqual((d.method, d.bank_name, d.iban), ("cash", "", ""))
        self.assertEqual(PaymentDetail.objects.count(), 1)

    def test_the_bank_details_must_be_right(self):
        with self.assertRaisesMessage(PayrollError, "Enter the bank name"):
            services.save_payment_details(self.emp, method="bank", bank_name="", iban=VALID_IBAN)
        with self.assertRaisesMessage(PayrollError, "does not look like an IBAN"):
            services.save_payment_details(self.emp, method="bank", bank_name="NBB", iban="12345")
        with self.assertRaisesMessage(PayrollError, "check digits"):
            services.save_payment_details(self.emp, method="bank", bank_name="NBB", iban="BH68BMAG00001299123456")
        with self.assertRaisesMessage(PayrollError, "22 characters"):
            services.save_payment_details(self.emp, method="bank", bank_name="NBB", iban="BH67BMAG000012991234567")
        with self.assertRaisesMessage(PayrollError, "bank transfer or cash"):
            services.save_payment_details(self.emp, method="cheque")
        self.assertFalse(PaymentDetail.objects.exists())

    def test_contractor_supplied_labor_is_not_paid_through_payroll(self):
        worker = self.person("Ravi", "L-1", worker_type="labor")
        make_profile(worker, "contracted")
        with self.assertRaisesMessage(PayrollError, "paid through the contractor"):
            services.save_payment_details(worker, method="cash")
        direct = self.person("Sunil", "L-2", worker_type="labor")
        make_profile(direct, "direct")
        services.save_payment_details(direct, method="cash")                                # direct labor is fine
        self.assertTrue(PaymentDetail.objects.filter(employee=direct).exists())


class CoverageTests(PayrollCase):
    def setUp(self):
        super().setUp()
        self.hr = User.objects.create_user("hr")
        CompanyAccess.objects.create(user=self.hr, company=self.co)

    def test_who_payroll_covers(self):
        direct = self.person("Sunil", "L-2", worker_type="labor")
        make_profile(direct, "direct")
        contracted = self.person("Ravi", "L-1", worker_type="labor")
        make_profile(contracted, "contracted")
        unprofiled = self.person("New", "L-3", worker_type="labor")                         # no profile yet: still ours
        gone = self.person("Gone", "S-3", status="separated")
        foreign = self.person("Zed", "Z-1", company=self.other)
        covered = set(services.in_payroll(self.hr).values_list("pk", flat=True))
        self.assertEqual(covered, {self.emp.pk, direct.pk, unprofiled.pk})
        self.assertNotIn(contracted.pk, covered)
        self.assertNotIn(gone.pk, covered)
        self.assertNotIn(foreign.pk, covered)
        with_gone = set(services.in_payroll(self.hr, include_separated=True).values_list("pk", flat=True))
        self.assertIn(gone.pk, with_gone)
        self.assertNotIn(contracted.pk, with_gone)

    def test_the_gaps_to_close(self):
        second = self.person("Omar", "S-2")
        today = date(2025, 3, 1)
        self.assertEqual(services.coverage(self.hr, today), {
            "total": 2, "staff_total": 2, "staff_without_salary": 2, "without_payment": 2})
        self.pay(self.emp)
        services.save_payment_details(self.emp, method="cash")
        self.assertEqual(services.coverage(self.hr, today), {
            "total": 2, "staff_total": 2, "staff_without_salary": 1, "without_payment": 1})
        self.pay(second, start=date(2026, 1, 1))                                            # starts later: not paid yet
        self.assertEqual(services.coverage(self.hr, today)["staff_without_salary"], 1)
        self.assertEqual(services.coverage(self.hr, date(2026, 2, 1))["staff_without_salary"], 0)


class RegistrationTests(TestCase):
    def test_the_payroll_tab_and_every_right_it_offers_exist(self):
        module = registry.get_module("payroll")
        self.assertIsNotNone(module)
        self.assertEqual(module.label, "Payroll")
        self.assertGreaterEqual(len(module.capabilities), 5)
        for cap in module.capabilities:
            app_label, codename = cap.perm.split(".", 1)
            self.assertTrue(Permission.objects.filter(content_type__app_label=app_label, codename=codename).exists(),
                            cap.perm)

    def test_nobody_gets_payroll_rights_by_default(self):
        from django.contrib.auth.models import Group
        for group in Group.objects.all():
            self.assertFalse(group.permissions.filter(content_type__app_label="payroll").exists(), group.name)
