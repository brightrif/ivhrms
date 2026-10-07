import re
from datetime import date
from decimal import Decimal
from pathlib import Path

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import TestCase
from django.urls import reverse

from apps.employees.models import Employee
from apps.labor import services
from apps.labor.models import Contractor, LaborProfile, Trade
from apps.organization.models import Company, CompanyAccess

User = get_user_model()


class LaborPageCase(TestCase):
    def setUp(self):
        self.co = Company.objects.create(code="IV1", name="IV One")
        self.other_co = Company.objects.create(code="IV2", name="IV Two")
        self.mason = Trade.objects.create(code="MASON", name="Mason")
        self.contractor = Contractor.objects.create(company=self.co, name="Gulf Manpower")
        # the standard groups are filled in by migrate, exactly as on a real install
        self.hr = self.user("hr", "HR", self.co)
        self.finance = self.user("fin", "Finance", self.co)
        self.boss = self.user("boss", "Management", self.co)
        self.outsider = self.user("outsider", "HR", self.other_co)
        self.nobody = User.objects.create_user("nobody")
        self.client.force_login(self.hr)
        # HR also adds staff in the real system; the stand-in project has no staff group, so grant that permission
        self.hr.user_permissions.add(Permission.objects.get(codename="add_employee"))

    def user(self, name, group, company):
        u = User.objects.create_user(name)
        u.groups.add(Group.objects.get(name=group))
        CompanyAccess.objects.create(user=u, company=company)
        return u

    def url(self, name, *args):
        return reverse(f"web:{name}", args=args)

    def worker(self, name="Ravi", engagement="direct", contractor=None, company=None, rate="5"):
        e = Employee(company=company or self.co, first_name=name, joining_date=date(2026, 1, 1))
        return services.create_labor_worker(e, engagement=engagement, contractor=contractor, trade=self.mason,
                                            wage_basis="daily", rate=Decimal(rate))

    def new_worker_post(self, **over):
        data = {"company": self.co.pk, "first_name": "Sunil", "last_name": "K", "nationality": "Indian",
                "phone": "3999", "joining_date": "2026-03-01", "engagement": "direct", "trade": self.mason.pk,
                "wage_basis": "daily", "rate": "4.500", "standard_hours": "8", "overtime_eligible": "on"}
        data.update(over)
        return self.client.post(self.url("labor_create"), data)


class AccessTests(LaborPageCase):
    def test_every_page_needs_the_right_permission(self):
        p = self.worker()
        pages = [self.url(n) for n in ("labor_list", "labor_create", "labor_contractor_list",
                                       "labor_contractor_create", "labor_trade_list", "labor_trade_create")]
        pages += [self.url("labor_detail", p.pk), self.url("labor_edit", p.pk), self.url("labor_rate_change", p.pk),
                  self.url("labor_contractor_edit", self.contractor.pk), self.url("labor_trade_edit", self.mason.pk)]
        self.client.logout()
        for url in pages:
            self.assertEqual(self.client.get(url).status_code, 302, url)            # sent to log in
        self.client.force_login(self.nobody)
        for url in pages:
            self.assertEqual(self.client.get(url).status_code, 403, url)

    def test_finance_and_management_can_look_but_not_change(self):
        p = self.worker()
        for user in (self.finance, self.boss):
            self.client.force_login(user)
            for name in ("labor_list", "labor_contractor_list", "labor_trade_list"):
                self.assertEqual(self.client.get(self.url(name)).status_code, 200, (user.username, name))
            self.assertEqual(self.client.get(self.url("labor_detail", p.pk)).status_code, 200)
            for url in (self.url("labor_create"), self.url("labor_edit", p.pk), self.url("labor_rate_change", p.pk),
                        self.url("labor_contractor_create"), self.url("labor_trade_create")):
                self.assertEqual(self.client.get(url).status_code, 403, (user.username, url))
            self.assertEqual(self.client.post(self.url("labor_trade_toggle", self.mason.pk)).status_code, 403)

    def test_pay_is_hidden_from_someone_who_may_not_see_rates(self):
        self.worker(rate="7.250")
        viewer = User.objects.create_user("viewer")
        viewer.user_permissions.add(*Permission.objects.filter(codename__in=["view_laborprofile"]))
        CompanyAccess.objects.create(user=viewer, company=self.co)
        self.assertContains(self.client.get(self.url("labor_list")), "7.250")
        self.client.force_login(viewer)
        r = self.client.get(self.url("labor_list"))
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, "7.250")
        p = LaborProfile.objects.get()
        self.assertNotContains(self.client.get(self.url("labor_detail", p.pk)), "7.250")

    def test_a_user_only_sees_their_own_companies(self):
        mine = self.worker("Mine")
        theirs = self.worker("Theirs", company=self.other_co)
        r = self.client.get(self.url("labor_list"))
        self.assertContains(r, "Mine")
        self.assertNotContains(r, "Theirs")
        self.assertEqual(self.client.get(self.url("labor_detail", theirs.pk)).status_code, 404)
        self.assertEqual(self.client.post(self.url("labor_rate_change", theirs.pk), {}).status_code, 404)
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(self.url("labor_detail", mine.pk)).status_code, 404)
        self.assertNotContains(self.client.get(self.url("labor_contractor_list")), "Gulf Manpower")
        self.assertEqual(self.client.get(self.url("labor_contractor_edit", self.contractor.pk)).status_code, 404)


class WorkerPageTests(LaborPageCase):
    def test_add_a_worker(self):
        r = self.new_worker_post()
        p = LaborProfile.objects.get()
        self.assertRedirects(r, self.url("labor_detail", p.pk))
        self.assertEqual((p.employee.first_name, p.employee.worker_type, p.engagement), ("Sunil", "labor", "direct"))
        self.assertEqual(p.current_rate.rate, Decimal("4.500"))
        detail = self.client.get(self.url("labor_detail", p.pk))
        self.assertContains(detail, "4.500")
        self.assertContains(detail, "Sunil K")

    def test_a_broken_rule_shows_on_the_form_and_saves_nothing(self):
        r = self.new_worker_post(contractor=self.contractor.pk)                     # direct + contractor
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "no contractor")
        r = self.new_worker_post(engagement="contracted", wage_basis="monthly")
        self.assertContains(r, "paid a daily rate")
        r = self.new_worker_post(engagement="contracted", contractor=self.contractor.pk)      # this one is fine
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Employee.objects.count(), 1)

    def test_contractor_from_another_company_is_refused(self):
        foreign = Contractor.objects.create(company=self.other_co, name="Foreign")
        self.hr.company_access.create(company=self.other_co)
        r = self.new_worker_post(engagement="contracted", contractor=foreign.pk)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "another company")
        self.assertEqual(Employee.objects.count(), 0)

    def test_search_and_filters(self):
        self.worker("Ravi")
        self.worker("Imran", engagement="contracted", contractor=self.contractor)
        content = lambda **q: self.client.get(self.url("labor_list"), q).content.decode()
        self.assertIn("Ravi", content(q="rav"))
        self.assertNotIn("Imran", content(q="rav"))
        self.assertIn("Imran", content(q="gulf"))                    # by contractor name
        self.assertNotIn("Ravi", content(engagement="contracted"))
        self.assertIn("Ravi", content(trade=self.mason.pk))

    def test_workers_who_left_show_only_when_asked_for(self):
        p = self.worker("Gone")
        Employee.objects.filter(pk=p.employee_id).update(status="separated")
        self.assertNotContains(self.client.get(self.url("labor_list")), "Gone")
        self.assertContains(self.client.get(self.url("labor_list"), {"status": "separated"}), "Gone")

    def test_labor_employees_without_a_profile_are_flagged_and_can_be_set_up(self):
        e = Employee.objects.create(company=self.co, first_name="Old", worker_type="labor", joining_date=date(2025, 5, 1))
        page = self.client.get(self.url("labor_list"))
        self.assertContains(page, "without a labor profile")
        self.assertContains(page, self.url("labor_setup", e.pk))
        form = self.client.get(self.url("labor_setup", e.pk))
        self.assertEqual(form.status_code, 200)
        r = self.client.post(self.url("labor_setup", e.pk), {
            "engagement": "direct", "trade": self.mason.pk, "wage_basis": "monthly", "rate": "250.000",
            "standard_hours": "8", "effective_from": "2025-05-01", "overtime_eligible": "on"})
        p = LaborProfile.objects.get(employee=e)
        self.assertRedirects(r, self.url("labor_detail", p.pk))
        self.assertNotContains(self.client.get(self.url("labor_list")), "without a labor profile")
        self.assertRedirects(self.client.get(self.url("labor_setup", e.pk)), self.url("labor_detail", p.pk))

    def test_setup_is_only_for_labor_in_your_companies(self):
        staff = Employee.objects.create(company=self.co, first_name="Office", joining_date=date(2025, 5, 1))
        foreign = Employee.objects.create(company=self.other_co, first_name="F", worker_type="labor",
                                          joining_date=date(2025, 5, 1))
        self.assertEqual(self.client.get(self.url("labor_setup", staff.pk)).status_code, 404)
        self.assertEqual(self.client.get(self.url("labor_setup", foreign.pk)).status_code, 404)

    def test_edit_engagement_and_trade(self):
        p = self.worker()
        electrician = Trade.objects.create(code="ELEC", name="Electrician")
        r = self.client.post(self.url("labor_edit", p.pk), {
            "engagement": "contracted", "contractor": self.contractor.pk, "trade": electrician.pk, "notes": "Night crew"})
        self.assertRedirects(r, self.url("labor_detail", p.pk))
        p.refresh_from_db()
        self.assertEqual((p.engagement, p.contractor, p.trade, p.notes),
                         ("contracted", self.contractor, electrician, "Night crew"))

    def test_edit_rejects_a_contractor_on_a_direct_worker(self):
        p = self.worker()
        r = self.client.post(self.url("labor_edit", p.pk), {
            "engagement": "direct", "contractor": self.contractor.pk, "trade": self.mason.pk})
        self.assertContains(r, "no contractor")
        p.refresh_from_db()
        self.assertIsNone(p.contractor)

    def test_change_pay_keeps_the_history(self):
        p = self.worker(rate="5")
        r = self.client.post(self.url("labor_rate_change", p.pk), {
            "effective_from": "2026-07-01", "wage_basis": "daily", "rate": "6.000", "standard_hours": "9",
            "overtime_eligible": "on"})
        self.assertRedirects(r, self.url("labor_detail", p.pk))
        detail = self.client.get(self.url("labor_detail", p.pk))
        self.assertContains(detail, "6.000")
        self.assertContains(detail, "5.000")                           # the old terms are still listed
        self.assertContains(detail, "30 Jun 2026")                      # and end the day before the new ones
        bad = self.client.post(self.url("labor_rate_change", p.pk), {
            "effective_from": "2026-02-01", "wage_basis": "daily", "rate": "9.000", "standard_hours": "8"})
        self.assertContains(bad, "must start after")
        self.assertEqual(p.rates.count(), 2)

    def test_contracted_worker_is_offered_only_a_daily_rate(self):
        p = self.worker(engagement="contracted")
        form = self.client.get(self.url("labor_rate_change", p.pk)).context["form"]
        self.assertEqual([c[0] for c in form.fields["wage_basis"].choices], ["daily"])

    def test_pages_for_a_worker_who_left_hide_the_change_buttons(self):
        p = self.worker()
        Employee.objects.filter(pk=p.employee_id).update(status="separated")
        page = self.client.get(self.url("labor_detail", p.pk))
        self.assertNotContains(page, self.url("labor_rate_change", p.pk))
        r = self.client.post(self.url("labor_rate_change", p.pk), {
            "effective_from": "2026-07-01", "wage_basis": "daily", "rate": "6.000", "standard_hours": "8"})
        self.assertContains(r, "has left")


class ContractorAndTradePageTests(LaborPageCase):
    def test_add_edit_and_list_a_contractor(self):
        r = self.client.post(self.url("labor_contractor_create"), {
            "company": self.co.pk, "name": "  Al   Noor  Supply ", "phone": "1777", "cr_number": "12345-1"})
        self.assertRedirects(r, self.url("labor_contractor_list"))
        c = Contractor.objects.get(phone="1777")
        self.assertEqual(c.name, "Al Noor Supply")                       # stray spaces are tidied
        self.client.post(self.url("labor_contractor_edit", c.pk), {
            "company": self.other_co.pk, "name": "Al Noor Supply Co", "phone": "1888"})
        c.refresh_from_db()
        self.assertEqual((c.name, c.phone, c.company), ("Al Noor Supply Co", "1888", self.co))   # company cannot move
        self.assertContains(self.client.get(self.url("labor_contractor_list")), "Al Noor Supply Co")

    def test_duplicate_contractor_name_is_refused(self):
        r = self.client.post(self.url("labor_contractor_create"), {"company": self.co.pk, "name": "GULF MANPOWER"})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "already has a contractor")
        self.assertEqual(Contractor.objects.filter(company=self.co).count(), 1)

    def test_deactivating_a_contractor_with_workers_is_blocked_with_a_reason(self):
        p = self.worker(engagement="contracted", contractor=self.contractor)
        r = self.client.post(self.url("labor_contractor_toggle", self.contractor.pk), follow=True)
        self.assertContains(r, "still supplied")
        self.contractor.refresh_from_db()
        self.assertTrue(self.contractor.is_active)
        Employee.objects.filter(pk=p.employee_id).update(status="separated")
        self.client.post(self.url("labor_contractor_toggle", self.contractor.pk))
        self.contractor.refresh_from_db()
        self.assertFalse(self.contractor.is_active)
        self.client.post(self.url("labor_contractor_toggle", self.contractor.pk))
        self.contractor.refresh_from_db()
        self.assertTrue(self.contractor.is_active)

    def test_toggle_needs_post(self):
        self.assertEqual(self.client.get(self.url("labor_contractor_toggle", self.contractor.pk)).status_code, 405)
        self.assertEqual(self.client.get(self.url("labor_trade_toggle", self.mason.pk)).status_code, 405)

    def test_add_edit_and_deactivate_a_trade(self):
        r = self.client.post(self.url("labor_trade_create"), {"code": " elec ", "name": "Electrician"})
        self.assertRedirects(r, self.url("labor_trade_list"))
        t = Trade.objects.get(name="Electrician")
        self.assertEqual(t.code, "ELEC")
        dup = self.client.post(self.url("labor_trade_create"), {"code": "ELEC", "name": "Other"})
        self.assertContains(dup, "already a trade with this code")
        dup = self.client.post(self.url("labor_trade_create"), {"code": "E2", "name": "electrician"})
        self.assertContains(dup, "already a trade with this name")
        self.client.post(self.url("labor_trade_edit", t.pk), {"code": "ELEC", "name": "Electrician (LV)"})
        t.refresh_from_db()
        self.assertEqual(t.name, "Electrician (LV)")
        self.client.post(self.url("labor_trade_toggle", t.pk))
        t.refresh_from_db()
        self.assertFalse(t.is_active)

    def test_a_deactivated_trade_is_not_offered_for_new_workers_but_the_worker_keeps_it(self):
        p = self.worker()
        self.mason.is_active = False
        self.mason.save()
        form = self.client.get(self.url("labor_create")).context["form"]
        self.assertNotIn(self.mason, form.fields["trade"].queryset)
        edit = self.client.get(self.url("labor_edit", p.pk)).context["form"]
        self.assertIn(self.mason, edit.fields["trade"].queryset)            # so editing notes does not lose it


class LayoutTests(LaborPageCase):
    def test_every_form_places_every_field_it_has(self):
        p = self.worker()
        e = Employee.objects.create(company=self.co, first_name="Old", worker_type="labor", joining_date=date(2025, 5, 1))
        pages = [self.url("labor_create"), self.url("labor_setup", e.pk), self.url("labor_edit", p.pk),
                 self.url("labor_rate_change", p.pk), self.url("labor_contractor_create"),
                 self.url("labor_contractor_edit", self.contractor.pk), self.url("labor_trade_create"),
                 self.url("labor_trade_edit", self.mason.pk)]
        for url in pages:
            r = self.client.get(url)
            self.assertEqual(r.status_code, 200, url)
            placed = {n for section in r.context["layout"] for row in section["rows"] for n in row}
            self.assertEqual(placed, set(r.context["form"].fields), url)

    def test_templates_use_only_the_current_styles(self):
        legacy = re.compile(r'class="(pill|muted|err|ok|big|head|cards|chips|scroll|pager|filters|inline-form|form|card narrow)[ "]')
        folder = Path(__file__).resolve().parent.parent / "templates" / "web" / "labor"
        for path in sorted(folder.glob("*.html")):
            text = path.read_text(encoding="utf-8")
            self.assertIsNone(legacy.search(text), path.name)
            for fragment in ('btn small', 'btn ghost', 'btn danger', 'class="btn"', 'class="facts"', "<table>"):
                self.assertNotIn(fragment, text, f"{path.name}: {fragment}")
