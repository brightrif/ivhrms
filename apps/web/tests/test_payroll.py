from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.template import Context, Template
from django.test import RequestFactory, TestCase
from django.urls import reverse

from apps.employees.models import Employee
from apps.organization.models import Company, CompanyAccess
from apps.payroll import services
from apps.payroll.models import AllowanceType, PaymentDetail, SalaryRecord
from apps.payroll.settings_spec import allowance_context
from apps.payroll.tests import VALID_IBAN, make_profile

D = Decimal
User = get_user_model()

VIEW = ["view_salaryrecord", "view_allowancetype"]
SET = ["add_salaryrecord"]
CORRECT = ["change_salaryrecord"]
ALLOWANCES = ["add_allowancetype"]


class PayrollPageCase(TestCase):
    def setUp(self):
        self.co = Company.objects.create(code="IV1", name="IV One")
        self.other = Company.objects.create(code="IV2", name="IV Two")
        self.hr = self.user("hr", VIEW + SET + CORRECT + ALLOWANCES)
        self.viewer = self.user("viewer", VIEW)
        self.nobody = User.objects.create_user("nobody")
        self.outsider = self.user("outsider", VIEW + SET + CORRECT + ALLOWANCES, company=self.other)
        self.emp = self.person("Sara", "S-1")
        self.housing = AllowanceType.objects.create(company=self.co, name="Housing")
        self.transport = AllowanceType.objects.create(company=self.co, name="Transport")
        self.client.force_login(self.hr)

    def user(self, name, codenames, company=None):
        u = User.objects.create_user(name)
        CompanyAccess.objects.create(user=u, company=company or self.co)
        u.user_permissions.add(*Permission.objects.filter(content_type__app_label="payroll", codename__in=codenames))
        return u

    def person(self, first, no, company=None, **kw):
        kw.setdefault("joining_date", date(2020, 1, 1))
        return Employee.objects.create(company=company or self.co, employee_no=no, first_name=first, **kw)

    def url(self, name, *args):
        return reverse(f"web:{name}", args=args)

    def pay(self, emp=None, start=date(2024, 1, 1), basic="500", allowances=(), reason="joining"):
        return services.change_salary(emp or self.emp, effective_from=start, basic=D(basic),
                                      allowances=list(allowances), reason=reason)

    def salary_post(self, emp=None, **over):
        data = {"effective_from": "2025-01-01", "reason": "increase", "basic": "600", "remarks": ""}
        data.update(over)
        return self.client.post(self.url("payroll_salary_change", (emp or self.emp).pk), data)


class AccessTests(PayrollPageCase):
    def test_every_page_asks_for_a_sign_in_and_the_right(self):
        pages = [self.url("payroll_home"), self.url("payroll_employee", self.emp.pk),
                 self.url("payroll_salary_change", self.emp.pk), self.url("payroll_salary_correct", self.emp.pk),
                 self.url("payroll_payment_edit", self.emp.pk), self.url("payroll_allowances"),
                 self.url("payroll_allowance_create"), self.url("payroll_allowance_edit", self.housing.pk)]
        self.client.logout()
        for page in pages:
            self.assertEqual(self.client.get(page).status_code, 302, page)
        self.client.force_login(self.nobody)
        for page in pages:
            self.assertEqual(self.client.get(page).status_code, 403, page)

    def test_a_viewer_may_look_but_not_change_anything(self):
        self.pay()
        self.client.force_login(self.viewer)
        for name in ("payroll_home", "payroll_allowances"):
            self.assertEqual(self.client.get(self.url(name)).status_code, 200, name)
        self.assertEqual(self.client.get(self.url("payroll_employee", self.emp.pk)).status_code, 200)
        for name, args in (("payroll_salary_change", (self.emp.pk,)), ("payroll_salary_correct", (self.emp.pk,)),
                           ("payroll_payment_edit", (self.emp.pk,)), ("payroll_allowance_create", ()),
                           ("payroll_allowance_edit", (self.housing.pk,))):
            self.assertEqual(self.client.get(self.url(name, *args)).status_code, 403, name)
        self.assertEqual(self.client.post(self.url("payroll_allowance_toggle", self.housing.pk)).status_code, 403)
        self.assertEqual(self.salary_post().status_code, 403)
        self.assertEqual(SalaryRecord.objects.count(), 1)
        page = self.client.get(self.url("payroll_employee", self.emp.pk))
        for button in ("Change salary", "Correct latest"):
            self.assertNotContains(page, button)

    def test_another_companys_people_and_allowances_are_not_found(self):
        foreign = self.person("Zed", "Z-1", company=self.other)
        theirs = AllowanceType.objects.create(company=self.other, name="Housing")
        for page in (self.url("payroll_employee", foreign.pk), self.url("payroll_salary_change", foreign.pk),
                     self.url("payroll_payment_edit", foreign.pk), self.url("payroll_allowance_edit", theirs.pk)):
            self.assertEqual(self.client.get(page).status_code, 404, page)
        self.assertEqual(self.client.post(self.url("payroll_allowance_toggle", theirs.pk)).status_code, 404)
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(self.url("payroll_employee", self.emp.pk)).status_code, 404)

    def test_the_sidebar_link_shows_only_to_people_who_may_open_payroll(self):
        from django.template.loader import render_to_string

        link = f'href="{self.url("payroll_home")}"'

        def sidebar(user):
            request = RequestFactory().get("/")
            request.user = user
            return render_to_string("web/base.html", request=request)

        self.assertIn(link, sidebar(self.hr))
        self.assertIn(link, sidebar(self.viewer))
        self.assertNotIn(link, sidebar(self.nobody))


class PeopleListTests(PayrollPageCase):
    def setUp(self):
        super().setUp()
        self.pay(allowances=[(self.housing, D("150"))])
        self.omar = self.person("Omar", "S-2")
        self.direct = self.person("Sunil", "L-2", worker_type="labor")
        make_profile(self.direct, "direct")
        self.contracted = self.person("Ravi", "L-1", worker_type="labor")
        make_profile(self.contracted, "contracted")
        self.gone = self.person("Gone", "S-9", status="separated")
        services.save_payment_details(self.emp, method="bank", bank_name="NBB", iban=VALID_IBAN)

    def test_lists_staff_and_direct_labor_only(self):
        page = self.client.get(self.url("payroll_home"))
        self.assertContains(page, "Sara")
        self.assertContains(page, "650.000")                       # basic 500 + housing 150
        self.assertContains(page, "NBB")
        self.assertContains(page, "Omar")
        self.assertContains(page, "Sunil")
        self.assertContains(page, "Pay terms under Labor")
        self.assertNotContains(page, "Ravi")                       # paid through the contractor
        self.assertNotContains(page, "Gone")                       # has left

    def test_the_gaps_are_counted_and_filter_the_list(self):
        page = self.client.get(self.url("payroll_home"))
        self.assertContains(page, "1 staff without a salary")
        self.assertContains(page, "2 without payment details")      # Omar and Sunil
        page = self.client.get(self.url("payroll_home"), {"show": "no_salary"})
        self.assertContains(page, "Omar")
        self.assertNotContains(page, "Sara")
        self.assertNotContains(page, "Sunil")                       # labor has no salary record to be missing
        page = self.client.get(self.url("payroll_home"), {"show": "no_payment"})
        self.assertContains(page, "Sunil")
        self.assertNotContains(page, "Sara")

    def test_search_and_type_filters(self):
        self.assertNotContains(self.client.get(self.url("payroll_home"), {"q": "omar"}), "Sunil")
        self.assertContains(self.client.get(self.url("payroll_home"), {"q": "L-2"}), "Sunil")
        page = self.client.get(self.url("payroll_home"), {"kind": "labor"})
        self.assertContains(page, "Sunil")
        self.assertNotContains(page, "Sara")

    def test_everyone_set_up_is_said_so(self):
        self.pay(self.omar)
        services.save_payment_details(self.omar, method="cash")
        services.save_payment_details(self.direct, method="cash")
        self.assertContains(self.client.get(self.url("payroll_home")), "Every person has a salary and payment details")

    def test_a_long_list_is_paged_and_keeps_the_filters(self):
        for i in range(30):
            self.person(f"Extra{i:02d}", f"E-{i:02d}")
        page = self.client.get(self.url("payroll_home"), {"show": "no_salary"})
        self.assertContains(page, "Page 1 of 2")
        self.assertContains(page, "?show=no_salary&page=2")
        self.assertEqual(self.client.get(self.url("payroll_home"), {"show": "no_salary", "page": 2}).status_code, 200)


class PersonPageTests(PayrollPageCase):
    def test_a_staff_page_shows_salary_history_and_payment(self):
        self.pay(allowances=[(self.housing, D("150"))])
        self.pay(start=date(2025, 7, 1), basic="600", reason="increase")
        services.save_payment_details(self.emp, method="bank", bank_name="NBB", iban=VALID_IBAN)
        page = self.client.get(self.url("payroll_employee", self.emp.pk))
        for text in ("Salary history", "650.000", "Joining salary", "Increase", "Housing 150.000", "NBB",
                     "BH67 BMAG 0000 1299 1234 56"):
            self.assertContains(page, text)
        self.assertEqual(page.context["latest"].basic, D("600"))

    def test_a_future_salary_is_announced_not_applied(self):
        self.pay(start=date(2024, 1, 1))
        start = date.today() + timedelta(days=40)
        self.pay(start=start, basic="700", reason="increase")
        page = self.client.get(self.url("payroll_employee", self.emp.pk))
        self.assertContains(page, "A new salary of")
        self.assertEqual(page.context["current"].basic, D("500"))
        self.assertEqual(page.context["upcoming"].basic, D("700"))

    def test_a_labor_page_points_to_labor_and_has_no_salary_buttons(self):
        direct = self.person("Sunil", "L-2", worker_type="labor")
        make_profile(direct, "direct")
        page = self.client.get(self.url("payroll_employee", direct.pk))
        self.assertContains(page, "paid from the pay terms kept under Labor")
        self.assertNotContains(page, "Set salary")
        self.assertEqual(self.client.get(self.url("payroll_salary_change", direct.pk)).status_code, 404)
        contracted = self.person("Ravi", "L-1", worker_type="labor")
        make_profile(contracted, "contracted")
        self.assertEqual(self.client.get(self.url("payroll_employee", contracted.pk)).status_code, 404)
        self.assertEqual(self.client.get(self.url("payroll_payment_edit", contracted.pk)).status_code, 404)

    def test_someone_who_has_left_keeps_a_readable_history_but_no_new_salary(self):
        gone = self.person("Gone", "S-9")
        self.pay(gone)
        Employee.objects.filter(pk=gone.pk).update(status="separated")
        page = self.client.get(self.url("payroll_employee", gone.pk))
        self.assertContains(page, "Salary history")
        self.assertNotContains(page, "Change salary")
        r = self.client.get(self.url("payroll_salary_change", gone.pk))
        self.assertRedirects(r, self.url("payroll_employee", gone.pk), fetch_redirect_response=False)
        self.assertContains(self.client.get(r.url), "has left the company")


class SalaryFormTests(PayrollPageCase):
    def test_the_first_salary_starts_at_the_joining_date_and_offers_every_allowance(self):
        page = self.client.get(self.url("payroll_salary_change", self.emp.pk))
        form = page.context["form"]
        self.assertEqual((form.initial["effective_from"], form.initial["reason"]),
                         (self.emp.joining_date, "joining"))
        self.assertEqual(form.allowance_names, [f"allow_{self.housing.pk}", f"allow_{self.transport.pk}"])
        self.assertContains(page, "first salary on record")

    def test_saving_a_salary_with_allowances(self):
        r = self.salary_post(**{f"allow_{self.housing.pk}": "150", f"allow_{self.transport.pk}": "", "basic": "500"})
        self.assertRedirects(r, self.url("payroll_employee", self.emp.pk), fetch_redirect_response=False)
        record = SalaryRecord.objects.get()
        self.assertEqual((record.basic, record.total, record.reason), (D("500"), D("650"), "increase"))
        self.assertContains(self.client.get(r.url), "New salary saved")

    def test_the_second_salary_opens_with_the_current_figures_ready_to_change(self):
        self.pay(allowances=[(self.housing, D("150"))])
        form = self.client.get(self.url("payroll_salary_change", self.emp.pk)).context["form"]
        self.assertEqual(form.initial["basic"], D("500"))
        self.assertEqual(form.initial[f"allow_{self.housing.pk}"], D("150"))
        self.assertEqual((form.initial["reason"], form.initial["effective_from"]), ("increase", date.today()))
        r = self.salary_post(effective_from="2025-07-01", basic="550",
                             **{f"allow_{self.housing.pk}": "150"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(SalaryRecord.objects.get(effective_to__isnull=True).basic, D("550"))
        self.assertEqual(SalaryRecord.objects.get(effective_from=date(2024, 1, 1)).effective_to, date(2025, 6, 30))

    def test_a_rule_broken_is_shown_on_the_form_and_nothing_is_saved(self):
        self.pay()
        r = self.salary_post(effective_from="2024-01-01")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "must start after the current one")
        self.assertContains(r, "use Correct instead")
        self.assertEqual(SalaryRecord.objects.count(), 1)
        self.assertContains(self.salary_post(basic="0"), "Ensure this value is greater than or equal to 0.001")

    def test_a_company_with_no_allowance_types_is_told_where_to_add_them(self):
        AllowanceType.objects.all().delete()
        page = self.client.get(self.url("payroll_salary_change", self.emp.pk))
        self.assertContains(page, "no allowance types yet")
        self.assertEqual([s["title"] for s in page.context["layout"]], ["Salary"])

    def test_every_form_places_every_field_it_has(self):
        self.pay()
        pages = [self.url("payroll_salary_change", self.emp.pk), self.url("payroll_salary_correct", self.emp.pk),
                 self.url("payroll_payment_edit", self.emp.pk), self.url("payroll_allowance_create"),
                 self.url("payroll_allowance_edit", self.housing.pk)]
        for page in pages:
            r = self.client.get(page)
            placed = [n for section in r.context["layout"] for row in section["rows"] for n in row]
            self.assertEqual(set(placed), set(r.context["form"].fields), page)
            self.assertEqual(len(placed), len(set(placed)), page)


class CorrectionPageTests(PayrollPageCase):
    def test_correcting_needs_the_right_and_a_reason(self):
        self.pay(allowances=[(self.housing, D("150"))])
        url = self.url("payroll_salary_correct", self.emp.pk)
        self.client.force_login(self.user("setter", VIEW + SET))                # may set salaries, may not correct
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(self.hr)
        r = self.client.post(url, {"basic": "550", "remarks": "", f"allow_{self.housing.pk}": "150"})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "This field is required")
        r = self.client.post(url, {"basic": "550", "remarks": "Typo", f"allow_{self.housing.pk}": "160"})
        self.assertRedirects(r, self.url("payroll_employee", self.emp.pk))
        record = SalaryRecord.objects.get()
        self.assertEqual((record.basic, record.total, record.remarks), (D("550"), D("710"), "Typo"))

    def test_with_no_salary_yet_there_is_nothing_to_correct(self):
        r = self.client.get(self.url("payroll_salary_correct", self.emp.pk))
        self.assertRedirects(r, self.url("payroll_employee", self.emp.pk), fetch_redirect_response=False)
        self.assertContains(self.client.get(r.url), "no salary to correct")


class PaymentPageTests(PayrollPageCase):
    def post(self, **over):
        data = {"method": "bank", "bank_name": "NBB", "iban": "bh67 bmag 0000 1299 1234 56"}
        data.update(over)
        return self.client.post(self.url("payroll_payment_edit", self.emp.pk), data)

    def test_bank_details_are_saved_tidy_and_come_back_filled_in(self):
        self.assertRedirects(self.post(), self.url("payroll_employee", self.emp.pk))
        self.assertEqual(PaymentDetail.objects.get().iban, VALID_IBAN)
        form = self.client.get(self.url("payroll_payment_edit", self.emp.pk)).context["form"]
        self.assertEqual(form.initial["iban"], "BH67 BMAG 0000 1299 1234 56")

    def test_a_wrong_iban_or_missing_bank_is_refused_beside_the_field(self):
        self.assertContains(self.post(iban="BH00BMAG00001299123456"), "check digits")
        self.assertContains(self.post(bank_name="", iban=VALID_IBAN), "Enter the bank name")
        self.assertFalse(PaymentDetail.objects.exists())

    def test_cash_needs_nothing_else(self):
        self.assertEqual(self.post(method="cash", bank_name="", iban="").status_code, 302)
        self.assertEqual(PaymentDetail.objects.get().method, "cash")

    def test_direct_labor_can_be_given_payment_details_too(self):
        direct = self.person("Sunil", "L-2", worker_type="labor")
        make_profile(direct, "direct")
        r = self.client.post(self.url("payroll_payment_edit", direct.pk), {"method": "cash"})
        self.assertRedirects(r, self.url("payroll_employee", direct.pk))


class AllowancePageTests(PayrollPageCase):
    def test_list_shows_each_type_and_who_is_paid_it(self):
        self.pay(allowances=[(self.housing, D("150"))])
        page = self.client.get(self.url("payroll_allowances"))
        self.assertContains(page, "Housing")
        self.assertContains(page, "Transport")
        self.assertEqual({t.name: t.receiving for t in page.context["types"]}, {"Housing": 1, "Transport": 0})

    def test_adding_one_with_a_single_company_needs_no_company_choice(self):
        form = self.client.get(self.url("payroll_allowance_create")).context["form"]
        self.assertEqual(form.initial["company"], self.co.pk)
        r = self.client.post(self.url("payroll_allowance_create"),
                             {"company": self.co.pk, "name": "Food", "counts_for_sio": "on"})
        self.assertRedirects(r, self.url("payroll_allowances"))
        food = AllowanceType.objects.get(name="Food")
        self.assertEqual((food.company, food.counts_for_sio, food.is_active), (self.co, True, True))

    def test_a_duplicate_name_is_refused_on_the_form(self):
        r = self.client.post(self.url("payroll_allowance_create"), {"company": self.co.pk, "name": "housing"})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "already has an allowance")

    def test_editing_keeps_the_company_and_cannot_move_it(self):
        url = self.url("payroll_allowance_edit", self.housing.pk)
        r = self.client.post(url, {"company": self.other.pk, "name": "Housing allowance", "counts_for_sio": "on"})
        self.assertRedirects(r, self.url("payroll_allowances"))
        self.housing.refresh_from_db()
        self.assertEqual((self.housing.company, self.housing.name, self.housing.counts_for_sio),
                         (self.co, "Housing allowance", True))

    def test_switching_off_is_blocked_while_someone_is_paid_it(self):
        self.pay(allowances=[(self.housing, D("150"))])
        r = self.client.post(self.url("payroll_allowance_toggle", self.housing.pk), follow=True)
        self.assertContains(r, "1 person is paid Housing")
        self.housing.refresh_from_db()
        self.assertTrue(self.housing.is_active)
        r = self.client.post(self.url("payroll_allowance_toggle", self.transport.pk), follow=True)
        self.assertContains(r, "Transport is now off")
        self.assertFalse(AllowanceType.objects.get(pk=self.transport.pk).is_active)
        self.assertNotIn(f"allow_{self.transport.pk}",
                         self.client.get(self.url("payroll_salary_change", self.emp.pk)).context["form"].fields)
        self.assertEqual(self.client.get(self.url("payroll_allowance_toggle", self.transport.pk)).status_code, 405)


class SettingsAndStaffPageTests(PayrollPageCase):
    def test_the_settings_tab_lists_each_company_with_its_allowance_count(self):
        rows = allowance_context(self.hr)["allowance_rows"]
        self.assertEqual([(r["company"], r["active"], r["total"]) for r in rows], [(self.co, 2, 2)])
        AllowanceType.objects.filter(pk=self.transport.pk).update(is_active=False)
        self.assertEqual(allowance_context(self.hr)["allowance_rows"][0]["active"], 1)
        from django.template.loader import render_to_string
        html = render_to_string("web/settings/_payroll_allowances.html",
                                {"section": {"extra": allowance_context(self.hr)}, "perms": _Perms(self.hr)})
        self.assertIn("IV One", html)
        self.assertIn(self.url("payroll_allowance_create"), html)

    def render_card(self, user, employee):
        request = RequestFactory().get("/")
        request.user = user
        return Template("{% load payroll_tags %}{% employee_pay employee %}").render(
            Context({"request": request, "employee": employee, "perms": _Perms(user)}))

    def test_the_staff_page_card_shows_salary_only_to_those_who_may_see_it(self):
        self.pay(allowances=[(self.housing, D("150"))])
        html = self.render_card(self.hr, self.emp)
        self.assertIn("650.000", html)
        self.assertIn(self.url("payroll_employee", self.emp.pk), html)
        self.assertEqual(self.render_card(self.nobody, self.emp).strip(), "")

    def test_the_card_asks_for_a_salary_when_there_is_none_and_skips_contracted_labor(self):
        self.assertIn("Set salary", self.render_card(self.hr, self.emp))
        direct = self.person("Sunil", "L-2", worker_type="labor")
        make_profile(direct, "direct")
        self.assertIn("Pay terms under Labor", self.render_card(self.hr, direct))
        contracted = self.person("Ravi", "L-1", worker_type="labor")
        make_profile(contracted, "contracted")
        self.assertEqual(self.render_card(self.hr, contracted).strip(), "")


class _Perms:
    """What the template's `perms` gives: perms.payroll.add_salaryrecord -> the user's has_perm."""

    def __init__(self, user):
        self.user = user

    def __getitem__(self, app):
        user = self.user

        class App:
            def __getitem__(self, codename):
                return user.has_perm(f"{app}.{codename}")
        return App()
