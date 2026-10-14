import io
import zipfile
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.urls import reverse

from apps.attendance.models import Attendance
from apps.employees.models import Employee
from apps.organization.models import Company, CompanyAccess
from apps.payroll import policy as policies
from apps.payroll import runs, services
from apps.payroll.payrun import PayrollAdjustment, PayrollPolicy, PayrollRun
from apps.payroll.settings_spec import policy_context
from apps.payroll.test_runs import RunCase, approvals_of
from apps.payroll.tests import make_renewal_payment
from apps.vehicles.incidents import Fine
from apps.vehicles.models import Vehicle

D = Decimal
User = get_user_model()
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
VIEW = ["view_payrollrun", "view_payrollpolicy"]
PREPARE = ["add_payrollrun"]
SUBMIT = ["submit_payrollrun"]
PAY = ["pay_payrollrun"]
POLICY = ["add_payrollpolicy"]


def grant(user, *codenames):
    user.user_permissions.add(*Permission.objects.filter(content_type__app_label="payroll", codename__in=codenames))


class RunPageCase(RunCase):
    def setUp(self):
        super().setUp()
        grant(self.hr, *VIEW, *PREPARE, *SUBMIT, *PAY, *POLICY)
        self.viewer = self.user("viewer")
        grant(self.viewer, *VIEW)
        self.nobody = User.objects.create_user("nobody")
        self.outsider = User.objects.create_user("outsider")
        CompanyAccess.objects.create(user=self.outsider, company=self.other)
        grant(self.outsider, *VIEW, *PREPARE, *SUBMIT, *PAY, *POLICY)
        self.client.force_login(self.hr)

    def url(self, name, *args):
        return reverse(f"web:{name}", args=args)

    def draft_run(self):
        return self.new_run()

    def sheet_text(self, response):
        with zipfile.ZipFile(io.BytesIO(response.content)) as z:
            return z.read("xl/worksheets/sheet1.xml").decode()


class AccessTests(RunPageCase):
    def test_every_page_asks_for_a_sign_in_and_the_right(self):
        run = self.draft_run()
        line = run.lines.first()
        pages = [self.url("payroll_runs"), self.url("payroll_run_create"), self.url("payroll_run", run.pk),
                 self.url("payroll_line", run.pk, line.pk), self.url("payroll_adjustment_add", run.pk),
                 self.url("payroll_run_export", run.pk, "register"), self.url("payroll_policy"),
                 self.url("payroll_policy_new"), self.url("payroll_run_pay", run.pk, "bank")]
        self.client.logout()
        for page in pages:
            self.assertEqual(self.client.get(page).status_code, 302, page)
        self.client.force_login(self.nobody)
        for page in pages:
            self.assertEqual(self.client.get(page).status_code, 403, page)

    def test_a_viewer_may_look_but_not_act(self):
        run = self.draft_run()
        self.client.force_login(self.viewer)
        for page in (self.url("payroll_runs"), self.url("payroll_run", run.pk), self.url("payroll_policy"),
                     self.url("payroll_line", run.pk, run.lines.first().pk),
                     self.url("payroll_run_export", run.pk, "bank")):
            self.assertEqual(self.client.get(page).status_code, 200, page)
        for name in ("payroll_run_recalculate", "payroll_run_cancel", "payroll_run_submit", "payroll_run_charges"):
            self.assertEqual(self.client.post(self.url(name, run.pk)).status_code, 403, name)
        self.assertEqual(self.client.get(self.url("payroll_adjustment_add", run.pk)).status_code, 403)
        self.assertEqual(self.client.get(self.url("payroll_run_create")).status_code, 403)
        self.assertEqual(self.client.get(self.url("payroll_policy_new")).status_code, 403)
        page = self.client.get(self.url("payroll_run", run.pk))
        for button in ("Recalculate", "Send for approval", "Add adjustment"):
            self.assertNotContains(page, button)
        run.refresh_from_db()
        self.assertEqual(run.status, "draft")

    def test_preparing_sending_and_paying_are_separate_rights(self):
        run = self.draft_run()
        preparer = self.user("preparer")
        grant(preparer, *VIEW, *PREPARE)
        self.client.force_login(preparer)
        self.assertEqual(self.client.post(self.url("payroll_run_recalculate", run.pk)).status_code, 302)
        self.assertEqual(self.client.post(self.url("payroll_run_submit", run.pk)).status_code, 403)
        self.approve(run)
        self.assertEqual(self.client.get(self.url("payroll_run_pay", run.pk, "bank")).status_code, 403)

    def test_another_companys_runs_are_not_found(self):
        run = self.draft_run()
        self.client.force_login(self.outsider)
        for page in (self.url("payroll_run", run.pk), self.url("payroll_line", run.pk, run.lines.first().pk),
                     self.url("payroll_run_export", run.pk, "register"), self.url("payroll_adjustment_add", run.pk)):
            self.assertEqual(self.client.get(page).status_code, 404, page)
        self.assertEqual(self.client.post(self.url("payroll_run_recalculate", run.pk)).status_code, 404)
        self.assertNotContains(self.client.get(self.url("payroll_runs")), "March 2026")

    def test_someone_with_only_run_rights_still_gets_a_sidebar_link_that_works(self):
        from django.template.loader import render_to_string
        from django.test import RequestFactory

        def sidebar(user):
            request = RequestFactory().get("/")
            request.user = user
            return render_to_string("web/base.html", request=request)

        runs_link, people_link = f'href="{self.url("payroll_runs")}"', f'href="{self.url("payroll_home")}"'
        html = sidebar(self.viewer)                                  # may see runs, not salaries
        self.assertIn(runs_link, html)
        self.assertNotIn(people_link, html)
        self.assertIn(people_link, sidebar(self.hr_with_salaries()))
        self.assertNotIn(runs_link, sidebar(self.nobody))
        self.client.force_login(self.viewer)
        page = self.client.get(self.url("payroll_runs"))
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, people_link)                                       # no People tab to dead-end on

    def hr_with_salaries(self):
        user = self.user("salary_viewer")
        grant(user, "view_salaryrecord")
        return user

    def test_actions_are_posts_only(self):
        run = self.draft_run()
        for name in ("payroll_run_recalculate", "payroll_run_cancel", "payroll_run_submit", "payroll_run_withdraw",
                     "payroll_run_charges"):
            self.assertEqual(self.client.get(self.url(name, run.pk)).status_code, 405, name)


class StartingARunTests(RunPageCase):
    def test_the_form_opens_on_last_month_and_creates_the_run(self):
        form = self.client.get(self.url("payroll_run_create")).context["form"]
        self.assertEqual(form.initial["company"], self.co.pk)
        self.present_all()
        r = self.client.post(self.url("payroll_run_create"), {"company": self.co.pk, "kind": "staff", "month": 3, "year": 2026})
        run = PayrollRun.objects.get()
        self.assertRedirects(r, self.url("payroll_run", run.pk), fetch_redirect_response=False)
        self.assertEqual((run.label, run.status, run.prepared_by), ("March 2026", "draft", self.hr))
        self.assertContains(self.client.get(r.url), "March 2026")

    def present_all(self):
        for e in (self.ali, self.sara, self.hr_emp):
            self.present(e)

    def test_a_broken_rule_is_a_form_error(self):
        r = self.client.post(self.url("payroll_run_create"), {"company": self.co.pk, "kind": "staff", "month": 3, "year": 2099})
        self.assertContains(r, "has not started yet")
        PayrollPolicy.objects.all().delete()
        r = self.client.post(self.url("payroll_run_create"), {"company": self.co.pk, "kind": "staff", "month": 3, "year": 2026})
        self.assertContains(r, "no payroll policy")
        self.assertFalse(PayrollRun.objects.exists())

    def test_the_second_run_for_a_month_is_refused_until_the_first_is_cancelled(self):
        self.present_all()
        data = {"company": self.co.pk, "kind": "staff", "month": 3, "year": 2026}
        self.client.post(self.url("payroll_run_create"), data)
        self.assertContains(self.client.post(self.url("payroll_run_create"), data), "already has a staff payroll run")
        run = PayrollRun.objects.get()
        r = self.client.post(self.url("payroll_run_cancel", run.pk))
        self.assertRedirects(r, self.url("payroll_runs"))
        self.assertEqual(self.client.post(self.url("payroll_run_create"), data).status_code, 302)

    def test_the_list_shows_runs_with_their_state(self):
        run = self.draft_run()
        page = self.client.get(self.url("payroll_runs"))
        self.assertContains(page, "March 2026")
        self.assertContains(page, "Draft")
        self.assertEqual(page.context["rows"][0]["totals"]["people"], 3)
        self.approve(run)
        self.assertContains(self.client.get(self.url("payroll_runs")), "Approved")


class RunPageTests(RunPageCase):
    def test_the_detail_page_shows_the_figures_the_problems_and_the_buttons(self):
        run = self.draft_run()
        PayrollPolicy.objects.filter(pk=self.policy.pk).update(sio_employee_rate=None)
        runs.calculate_run(run)
        page = self.client.get(self.url("payroll_run", run.pk))
        for text in ("Payroll March 2026", "IV One", "Ali", "Sara", "900.000", "SIO rates are not set",
                     "2 people have a problem to fix", "Recalculate", "Send for approval", "Add adjustment",
                     "Pick up charges"):
            self.assertContains(page, text)
        self.assertEqual(page.context["totals"]["blocking"], 2)
        self.assertContains(page, "table-danger")

    def test_a_clean_run_has_no_alarms(self):
        page = self.client.get(self.url("payroll_run", self.draft_run().pk))
        self.assertNotContains(page, "table-danger")
        self.assertNotContains(page, "problem to fix")

    def test_missing_attendance_is_an_amber_warning_that_does_not_stop_the_run(self):
        run = self.draft_run()
        Attendance.objects.filter(employee=self.ali, date=date(2026, 3, 4)).delete()
        runs.calculate_run(run)
        page = self.client.get(self.url("payroll_run", run.pk))
        self.assertContains(page, "table-warning")
        self.assertContains(page, "no attendance entry")
        self.assertEqual(page.context["totals"]["blocking"], 0)

    def test_the_line_page_explains_the_pay(self):
        run = self.draft_run()
        Attendance.objects.filter(employee=self.ali, date=date(2026, 3, 10)).update(status="absent")
        runs.calculate_run(run)
        line = run.lines.get(employee=self.ali)
        page = self.client.get(self.url("payroll_line", run.pk, line.pk))
        for text in ("How the pay is worked out", "Gross pay (31 of 31 days employed)", "30.000", "870.000",
                     "Days that cost pay", "2026-03-10", "salary &divide; 30"):
            self.assertContains(page, text)

    def test_a_line_from_another_run_is_not_found(self):
        run = self.draft_run()
        other_line = run.lines.first()
        runs.cancel_run(run)
        for e in (self.ali, self.sara, self.hr_emp):
            self.present(e, 2026, 4)
        second = runs.create_run(self.co, 2026, 4, user=self.hr)
        self.assertEqual(self.client.get(self.url("payroll_line", second.pk, other_line.pk)).status_code, 404)

    def test_recalculate_cancel_and_their_messages(self):
        run = self.draft_run()
        Attendance.objects.filter(employee=self.ali, date=date(2026, 3, 10)).update(status="absent")
        r = self.client.post(self.url("payroll_run_recalculate", run.pk))
        self.assertRedirects(r, self.url("payroll_run", run.pk), fetch_redirect_response=False)
        self.assertEqual(run.lines.get(employee=self.ali).absence_deduction, D("30.000"))
        self.assertContains(self.client.get(r.url), "Recalculated")
        self.approve(run)
        r = self.client.post(self.url("payroll_run_recalculate", run.pk), follow=True)
        self.assertContains(r, "approved, so it cannot be recalculated")
        r = self.client.post(self.url("payroll_run_cancel", run.pk), follow=True)
        self.assertContains(r, "cannot be cancelled")


class AdjustmentPageTests(RunPageCase):
    def test_adding_and_removing_an_adjustment(self):
        run = self.draft_run()
        page = self.client.get(self.url("payroll_adjustment_add", run.pk))
        self.assertEqual(set(page.context["form"].fields["employee"].queryset.values_list("pk", flat=True)),
                         {self.ali.pk, self.sara.pk, self.hr_emp.pk})
        data = {"employee": self.ali.pk, "kind": "deduction", "category": "advance", "label": "Advance", "amount": "100"}
        r = self.client.post(self.url("payroll_adjustment_add", run.pk), data)
        self.assertRedirects(r, self.url("payroll_run", run.pk), fetch_redirect_response=False)
        adjustment = PayrollAdjustment.objects.get()
        self.assertEqual(run.lines.get(employee=self.ali).net, D("800.000"))
        self.assertContains(self.client.get(r.url), "Advance")
        self.client.post(self.url("payroll_adjustment_remove", run.pk, adjustment.pk))
        self.assertEqual(run.lines.get(employee=self.ali).net, D("900.000"))

    def test_the_form_offers_only_manual_reasons_and_refuses_a_bad_amount(self):
        run = self.draft_run()
        categories = dict(self.client.get(self.url("payroll_adjustment_add", run.pk)).context["form"].fields["category"].choices)
        self.assertNotIn("fine", categories)
        self.assertNotIn("renewal", categories)
        r = self.client.post(self.url("payroll_adjustment_add", run.pk),
                             {"employee": self.ali.pk, "kind": "addition", "category": "bonus", "label": "x", "amount": "0"})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(PayrollAdjustment.objects.exists())

    def test_only_a_draft_can_be_adjusted(self):
        run = self.draft_run()
        self.approve(run)
        r = self.client.get(self.url("payroll_adjustment_add", run.pk), follow=True)
        self.assertContains(r, "Only a draft run can be adjusted")

    def test_picking_up_charges_from_compliance_and_vehicles(self):
        vehicle = Vehicle.objects.create(company=self.co, plate_number="123456")
        make_renewal_payment(self.ali, paid_on=date(2026, 3, 5), government_fee=D("40"), charged_to_employee=True)
        Fine.objects.create(vehicle=vehicle, driver=self.sara, fined_on=date(2026, 3, 8), reference="F1",
                            offence="Speeding", amount=D("20"), charged_to_employee=True)
        run = self.draft_run()
        page = self.client.get(self.url("payroll_run", run.pk))
        self.assertContains(page, "Pick up charges (2)")
        r = self.client.post(self.url("payroll_run_charges", run.pk), follow=True)
        self.assertContains(r, "2 charges added as deductions")
        self.assertContains(r, "Speeding")
        r = self.client.post(self.url("payroll_run_charges", run.pk), follow=True)
        self.assertContains(r, "There is nothing to add")


class ApprovalPageTests(RunPageCase):
    def test_send_for_approval_then_management_decides(self):
        run = self.draft_run()
        r = self.client.post(self.url("payroll_run_submit", run.pk), follow=True)
        self.assertContains(r, "Sent to Management")
        self.assertContains(r, "Waiting for Management to approve")
        self.assertContains(r, "Take back")
        self.assertNotContains(r, "Recalculate")
        self.assertEqual(self.client.post(self.url("payroll_run_recalculate", run.pk), follow=True).redirect_chain[-1][0],
                         self.url("payroll_run", run.pk))
        from apps.core import approvals
        approvals.decide(approvals_of(run).pk, self.boss, "approve")
        page = self.client.get(self.url("payroll_run", run.pk))
        self.assertContains(page, "Approved")
        self.assertContains(page, "Record bank transfers (2)")
        self.assertContains(page, "Record cash (1)")

    def test_taking_it_back_and_a_rejection_note(self):
        run = self.draft_run()
        self.client.post(self.url("payroll_run_submit", run.pk))
        r = self.client.post(self.url("payroll_run_withdraw", run.pk), follow=True)
        self.assertContains(r, "Taken back")
        self.client.post(self.url("payroll_run_submit", run.pk))
        from apps.core import approvals
        approvals.decide(approvals_of(run).pk, self.boss, "reject", comment="Recheck the absences")
        self.assertContains(self.client.get(self.url("payroll_run", run.pk)), "Rejected: Recheck the absences")

    def test_problems_and_a_missing_employee_link_are_messages(self):
        run = self.draft_run()
        PayrollPolicy.objects.filter(pk=self.policy.pk).update(sio_employee_rate=None)
        runs.calculate_run(run)
        r = self.client.post(self.url("payroll_run_submit", run.pk), follow=True)
        self.assertContains(r, "a problem to fix first")
        PayrollPolicy.objects.filter(pk=self.policy.pk).update(sio_employee_rate=D("5"))
        runs.calculate_run(run)
        grant(self.hr_no_emp, *VIEW, *SUBMIT)
        self.client.force_login(self.hr_no_emp)
        r = self.client.post(self.url("payroll_run_submit", run.pk), follow=True)
        self.assertContains(r, "not linked to an employee record")


class PaymentPageTests(RunPageCase):
    def setUp(self):
        super().setUp()
        self.payroll = self.approve(self.draft_run())

    def test_recording_the_bank_transfers_and_then_the_cash(self):
        page = self.client.get(self.url("payroll_run_pay", self.payroll.pk, "bank"))
        self.assertEqual(len(page.context["form"].fields["lines"].queryset), 2)
        offered = page.context["form"].fields["lines"].queryset
        self.assertEqual({l.employee_name for l in offered}, {"Ali", "Sara"})      # the HR officer is paid cash
        chosen = list(offered.values_list("pk", flat=True))
        r = self.client.post(self.url("payroll_run_pay", self.payroll.pk, "bank"),
                             {"lines": chosen, "paid_on": "2026-04-02"})
        self.assertRedirects(r, self.url("payroll_run", self.payroll.pk), fetch_redirect_response=False)
        self.assertContains(self.client.get(r.url), "2 payments recorded")
        cash = list(self.client.get(self.url("payroll_run_pay", self.payroll.pk, "cash"))
                    .context["form"].fields["lines"].queryset.values_list("pk", flat=True))
        self.client.post(self.url("payroll_run_pay", self.payroll.pk, "cash"), {"lines": cash, "paid_on": "2026-04-03"})
        self.payroll.refresh_from_db()
        self.assertEqual(self.payroll.status, "paid")
        self.assertContains(self.client.get(self.url("payroll_run", self.payroll.pk)), "Paid 03 Apr")

    def test_bad_input_and_the_wrong_state(self):
        r = self.client.post(self.url("payroll_run_pay", self.payroll.pk, "bank"), {"paid_on": "2026-04-02"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.client.get(self.url("payroll_run_pay", self.payroll.pk, "cheque")).status_code, 404)
        r = self.client.post(self.url("payroll_run_pay", self.payroll.pk, "bank"),
                             {"lines": list(self.payroll.lines.values_list("pk", flat=True)), "paid_on": "2099-01-01"})
        self.assertEqual(r.status_code, 200)
        draft_run = PayrollRun.objects.get(pk=self.payroll.pk)
        PayrollRun.objects.filter(pk=draft_run.pk).update(status="draft")
        r = self.client.get(self.url("payroll_run_pay", draft_run.pk, "bank"), follow=True)
        self.assertContains(r, "once the run is approved")


class ExportTests(RunPageCase):
    def test_the_three_downloads(self):
        run = self.draft_run()
        register = self.client.get(self.url("payroll_run_export", run.pk, "register"))
        self.assertEqual((register.status_code, register["Content-Type"]), (200, XLSX))
        self.assertEqual(register["Content-Disposition"], 'attachment; filename="payroll-register-iv1-2026-03.xlsx"')
        text = self.sheet_text(register)
        for expected in ("Payroll register: March 2026", "Ali", "Sara", "900", "Bank transfer", "Cash"):
            self.assertIn(expected, text)
        bank = self.sheet_text(self.client.get(self.url("payroll_run_export", run.pk, "bank")))
        self.assertIn(VALID_IBAN, bank)
        self.assertNotIn("Hana", bank)                                         # cash-paid people are not on the bank sheet
        cash = self.sheet_text(self.client.get(self.url("payroll_run_export", run.pk, "cash")))
        self.assertIn("Hana", cash)
        self.assertIn("Signature", cash)
        self.assertNotIn("Ali", cash)

    def test_an_unknown_download_is_not_found(self):
        self.assertEqual(self.client.get(self.url("payroll_run_export", self.draft_run().pk, "everything")).status_code, 404)


class PolicyPageTests(RunPageCase):
    def test_the_history_and_a_new_policy(self):
        page = self.client.get(self.url("payroll_policy"))
        self.assertContains(page, "Current")
        self.assertContains(page, "5%")
        r = self.client.post(self.url("payroll_policy_new"), {
            "company": self.co.pk, "effective_from": "2026-06-01", "days_in_month": 30, "sio_employee_rate": "6",
            "sio_employer_rate": "11", "sio_employer_rate_other": "", "sio_wage_floor": "", "sio_wage_cap": "4000"})
        self.assertRedirects(r, self.url("payroll_policy"), fetch_redirect_response=False)
        new = policies.policy_on(self.co, date(2026, 6, 1))
        self.assertEqual((new.sio_employee_rate, new.sio_wage_cap, new.sio_on_paid_days), (D("6"), D("4000"), False))
        self.assertEqual(policies.policy_on(self.co, date(2026, 5, 31)), self.policy)

    def test_empty_sio_rates_are_allowed_and_shown_as_not_set(self):
        self.client.post(self.url("payroll_policy_new"), {"company": self.co.pk, "effective_from": "2026-06-01",
                                                          "days_in_month": 30})
        self.assertContains(self.client.get(self.url("payroll_policy")), "Not set")

    def test_a_broken_rule_is_a_form_error(self):
        r = self.client.post(self.url("payroll_policy_new"), {"company": self.co.pk, "effective_from": "2019-01-01",
                                                              "days_in_month": 30})
        self.assertContains(r, "must start after the current one")
        self.assertEqual(PayrollPolicy.objects.count(), 1)

    def test_the_settings_tab_summarises_each_company(self):
        rows = policy_context(self.hr)["policy_rows"]
        self.assertEqual([(r["company"], r["policy"]) for r in rows], [(self.co, self.policy)])
        from django.template.loader import render_to_string

        class Perms:
            def __getitem__(self, app):
                return type("A", (), {"__getitem__": lambda s, c: True})()
        html = render_to_string("web/settings/_payroll_policy.html", {"section": {"extra": policy_context(self.hr)},
                                                                      "perms": Perms()})
        self.assertIn("30 days a month", html)
        self.assertIn("SIO rates set", html)
        PayrollPolicy.objects.filter(pk=self.policy.pk).update(sio_employee_rate=None)
        html = render_to_string("web/settings/_payroll_policy.html", {"section": {"extra": policy_context(self.hr)},
                                                                      "perms": Perms()})
        self.assertIn("SIO rates not set", html)


class FormTests(RunPageCase):
    def test_every_form_places_every_field_it_has(self):
        run = self.draft_run()

        def check(page):
            r = self.client.get(page)
            placed = [n for section in r.context["layout"] for row in section["rows"] for n in row]
            self.assertEqual(set(placed), set(r.context["form"].fields), page)
            self.assertEqual(len(placed), len(set(placed)), page)

        for page in (self.url("payroll_run_create"), self.url("payroll_adjustment_add", run.pk),
                     self.url("payroll_policy_new")):
            check(page)
        self.approve(run)
        check(self.url("payroll_run_pay", run.pk, "bank"))


class TabsTests(RunPageCase):
    def test_the_tabs_follow_the_rights(self):
        page = self.client.get(self.url("payroll_runs"))
        for tab in ("Payroll runs", "Policy"):
            self.assertContains(page, tab)
        grant(self.viewer, "view_salaryrecord")
        self.client.force_login(self.viewer)
        self.assertNotContains(self.client.get(self.url("payroll_home")), "Allowance types")


from apps.payroll.tests import VALID_IBAN  # noqa: E402  (kept at the bottom: only the export test uses it)
