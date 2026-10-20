from datetime import date
from decimal import Decimal
from unittest.mock import patch

from apps.core import approvals
from apps.payroll import loan_services as ls
from apps.payroll.loans import Loan
from apps.payroll.test_settlements import approval_of
from apps.web.tests.test_payroll_runs import RunPageCase, grant

D = Decimal
RIGHTS = ["view_loan", "add_loan", "submit_loan", "pay_loan", "agree_loan"]
Status = Loan.Status


class LoanPageCase(RunPageCase):
    def setUp(self):
        super().setUp()
        grant(self.hr, *RIGHTS)
        grant(self.viewer, "view_loan")
        grant(self.outsider, *RIGHTS)
        self.preparer = self.user("preparer")                         # may prepare, not agree verbally or pay out
        grant(self.preparer, "view_payrollrun", "view_loan", "add_loan", "submit_loan")

    def draft(self, **kw):
        return ls.create_loan(self.hr, self.ali, kind="loan", amount=D("1200"), months=12,
                              first_month=date(2026, 3, 1), **kw)

    def approved(self):
        return self.draft(verbal=True, agreed_by="Mr. Owner", agreed_on=date(2026, 2, 15))

    def active(self):
        return ls.record_payout(self.hr, self.approved(), date(2026, 2, 20), "TRF-1")

    def data(self, **over):
        data = {"employee": self.ali.pk, "kind": "loan", "amount": "1200", "months": "12", "month": "3", "year": "2026",
                "interest_rate": "", "reason": "Car repair", "how": "approval", "agreed_by": "", "agreed_on": ""}
        data.update(over)
        return data

    def post_new(self, **over):
        return self.client.post(self.url("payroll_loan_new"), self.data(**over))


class AccessTests(LoanPageCase):
    def test_the_pages_ask_for_a_sign_in_and_the_right(self):
        loan = self.approved()
        pages = [self.url("payroll_loans"), self.url("payroll_loan_employee_search"), self.url("payroll_loan_new"),
                 self.url("payroll_loan", loan.pk), self.url("payroll_loan_statement", loan.pk),
                 self.url("payroll_loan_edit", loan.pk), self.url("payroll_loan_payout", loan.pk)]
        self.client.logout()
        for page in pages:
            self.assertEqual(self.client.get(page).status_code, 302, page)
        self.client.force_login(self.nobody)
        for page in pages:
            self.assertEqual(self.client.get(page).status_code, 403, page)

    def test_someone_who_may_only_look_cannot_prepare_or_pay(self):
        loan = self.active()
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.get(self.url("payroll_loans")).status_code, 200)
        self.assertEqual(self.client.get(self.url("payroll_loan", loan.pk)).status_code, 200)
        self.assertEqual(self.client.get(self.url("payroll_loan_statement", loan.pk)).status_code, 200)
        for page in (self.url("payroll_loan_employee_search"), self.url("payroll_loan_new"), self.url("payroll_loan_payment", loan.pk),
                     self.url("payroll_loan_waive", loan.pk), self.url("payroll_loan_preclose", loan.pk)):
            self.assertEqual(self.client.get(page).status_code, 403, page)
        for page in (self.url("payroll_loan_submit", loan.pk), self.url("payroll_loan_skip", loan.pk)):
            self.assertEqual(self.client.post(page).status_code, 403, page)
        self.assertNotContains(self.client.get(self.url("payroll_loan", loan.pk)), "Record an early payment")

    def test_another_companys_loan_is_not_found(self):
        loan = self.active()
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(self.url("payroll_loan", loan.pk)).status_code, 404)
        self.assertEqual(self.client.post(self.url("payroll_loan_skip", loan.pk)).status_code, 404)
        self.assertNotContains(self.client.get(self.url("payroll_loans")), loan.number)

    def test_the_payroll_tabs_show_loans_only_to_people_who_may_see_them(self):
        self.assertContains(self.client.get(self.url("payroll_runs")), self.url("payroll_loans"))
        self.client.force_login(self.viewer)
        self.assertContains(self.client.get(self.url("payroll_loans")), "Loans")
        grant(self.nobody, "view_payrollrun")
        self.client.force_login(self.nobody)
        self.assertNotContains(self.client.get(self.url("payroll_runs")), self.url("payroll_loans"))


class ListTests(LoanPageCase):
    def test_the_list_shows_amount_repaid_balance_and_status(self):
        loan = self.active()
        page = self.client.get(self.url("payroll_loans"))
        for text in (loan.number, "Ali", "1200.000", "Active", "Mar 2026"):
            self.assertContains(page, text)
        self.assertEqual(page.context["page"][0].balance_now, D("1200.000"))

    def test_the_repaid_figure_is_not_multiplied_by_the_number_of_instalments(self):
        loan = self.active()
        ls.record_payment(self.hr, loan, D("100"), date(2026, 3, 5), "bank", "TRF-9")
        row = self.client.get(self.url("payroll_loans")).context["page"][0]
        self.assertEqual((row.repaid_sum, row.balance_now, str(row.next_month)), (D("100.000"), D("1100.000"), "2026-03-01"))

    def test_open_loans_by_default_and_the_filters_work(self):
        done = self.active()
        ls.waive(self.hr, done, "Written off")
        open_loan = self.draft()
        page = self.client.get(self.url("payroll_loans"))
        self.assertContains(page, open_loan.number)
        self.assertNotContains(page, done.number)
        self.assertContains(self.client.get(self.url("payroll_loans"), {"status": "closed"}), done.number)
        self.assertContains(self.client.get(self.url("payroll_loans"), {"status": "all"}), done.number)
        self.assertNotContains(self.client.get(self.url("payroll_loans"), {"q": "sara"}), open_loan.number)
        self.assertContains(self.client.get(self.url("payroll_loans"), {"q": "ali"}), open_loan.number)

    def test_the_search_box_lists_staff_and_labor_who_are_still_here_as_json(self):
        from apps.employees.models import Employee
        Employee.objects.filter(pk=self.sara.pk).update(status="separated")
        found = self.client.get(self.url("payroll_loan_employee_search"), {"q": "ali"}).json()["results"]
        self.assertEqual(found, [{"id": self.ali.pk, "name": "Ali", "code": self.ali.employee_no}])
        listed = [r["id"] for r in self.client.get(self.url("payroll_loan_employee_search")).json()["results"]]
        self.assertIn(self.ali.pk, listed)
        self.assertNotIn(self.sara.pk, listed)                                    # she left
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(self.url("payroll_loan_employee_search"), {"q": "ali"}).json()["results"], [])


class NewLoanTests(LoanPageCase):
    def test_the_form_is_the_standard_page_with_the_search_box_for_the_employee(self):
        page = self.client.get(self.url("payroll_loan_new"))
        self.assertTemplateUsed(page, "web/form_layout.html")
        for text in ("Search by name or number", "data-typeahead", "How was it agreed?", "Already agreed verbally",
                     "Yearly interest %", "First deduction month", "The schedule", "Create the loan"):
            self.assertContains(page, text)
        self.assertNotContains(page, "Why go over the limit?")                      # only after a limit is passed

    def test_a_link_from_a_persons_page_chooses_them(self):
        page = self.client.get(self.url("payroll_loan_new") + f"?employee={self.ali.pk}")
        self.assertEqual(page.context["form"].initial["employee"], self.ali.pk)

    def test_a_loan_for_approval_is_created_as_a_draft(self):
        resp = self.post_new()
        loan = Loan.objects.get()
        self.assertRedirects(resp, self.url("payroll_loan", loan.pk))
        self.assertEqual((loan.status, loan.reason, loan.employee), (Status.DRAFT, "Car repair", self.ali))

    def test_a_loan_agreed_verbally_starts_approved(self):
        resp = self.post_new(how="verbal", agreed_by="Mr. Owner", agreed_on="2026-02-15")
        loan = Loan.objects.get()
        self.assertEqual((loan.status, loan.agreed_by, loan.agreed_on), (Status.APPROVED, "Mr. Owner", date(2026, 2, 15)))
        self.assertEqual(loan.instalments.count(), 12)
        self.assertContains(self.client.get(resp.url), "Verbally by Mr. Owner on 15 Feb 2026")

    def test_verbal_needs_who_and_when(self):
        page = self.post_new(how="verbal")
        self.assertContains(page, "Say who agreed it.")
        self.assertContains(page, "Say when it was agreed.")
        self.assertFalse(Loan.objects.exists())

    def test_someone_without_the_verbal_right_is_not_offered_it_and_cannot_force_it(self):
        self.client.force_login(self.preparer)
        self.assertNotContains(self.client.get(self.url("payroll_loan_new")), "Already agreed verbally")
        self.post_new(how="verbal", agreed_by="Mr. Owner", agreed_on="2026-02-15")
        self.assertEqual(Loan.objects.get().status, Status.DRAFT)

    def test_bad_values_are_explained_on_the_form(self):
        page = self.post_new(amount="0")
        self.assertContains(page, "greater than or equal to")
        page = self.post_new(month="12", year="2019")
        self.assertContains(page, "before the month the person joined")
        self.assertFalse(Loan.objects.exists())

    def test_a_limit_shows_the_warning_and_asks_for_the_reason(self):
        limits = patch("apps.payroll.loan_services.loan_settings.limits_for",
                       return_value={"salaries": None, "months": 6, "share": None})
        with limits:
            page = self.post_new()
            self.assertContains(page, "This is over a limit set in Settings")
            self.assertContains(page, "more than the 6 allowed")
            self.assertContains(page, "Why go over the limit?")
            self.assertContains(page, "Give the reason to go ahead.")
            self.assertFalse(Loan.objects.exists())
            self.post_new(override_reason="Agreed by the owner")
        self.assertEqual(Loan.objects.get().override_reason, "Agreed by the owner")

    def test_a_person_who_left_cannot_have_a_loan(self):
        from apps.employees.models import Employee
        Employee.objects.filter(pk=self.ali.pk).update(status="separated")
        self.assertContains(self.post_new(), "Select a valid choice")
        self.assertFalse(Loan.objects.exists())


class LoanPageTests(LoanPageCase):
    def test_a_draft_shows_the_planned_schedule_and_its_buttons(self):
        loan = self.draft()
        page = self.client.get(self.url("payroll_loan", loan.pk))
        for text in ("Draft", "Planned, until it is approved", "Send for approval", "Change", "Cancel"):
            self.assertContains(page, text)
        self.assertNotContains(page, "Record an early payment")

    def test_the_whole_road_from_a_draft_to_payroll(self):
        loan = self.draft()
        self.client.post(self.url("payroll_loan_submit", loan.pk))
        loan.refresh_from_db()
        self.assertEqual(loan.status, Status.SUBMITTED)
        self.client.post(self.url("payroll_loan_withdraw", loan.pk))
        self.client.post(self.url("payroll_loan_submit", loan.pk))
        approvals.decide(approval_of(loan).pk, self.boss, "approve")
        page = self.client.get(self.url("payroll_loan", loan.pk))
        self.assertContains(page, "Approved, not paid out")
        self.assertContains(page, "Record the bank transfer")
        resp = self.client.post(self.url("payroll_loan_payout", loan.pk), {"paid_on": "2026-02-20", "reference": " "})
        self.assertContains(resp, "This field is required")
        resp = self.client.post(self.url("payroll_loan_payout", loan.pk), {"paid_on": "2026-02-20", "reference": "TRF-77"})
        self.assertRedirects(resp, self.url("payroll_loan", loan.pk))
        loan.refresh_from_db()
        self.assertEqual((loan.status, loan.payout_reference), (Status.ACTIVE, "TRF-77"))
        self.assertContains(self.client.get(resp.url), "Bank transfer on 20 Feb 2026, ref TRF-77")

    def test_a_draft_can_be_changed_and_cancelled(self):
        loan = self.draft()
        self.client.post(self.url("payroll_loan_edit", loan.pk), {
            "kind": "loan", "amount": "900", "months": "9", "month": "4", "year": "2026", "interest_rate": "2",
            "reason": "Changed"})
        loan.refresh_from_db()
        self.assertEqual((loan.amount, loan.months, loan.interest_rate, loan.interest_total),
                         (D("900"), 9, D("2"), D("13.500")))
        self.assertContains(self.client.get(self.url("payroll_loan_cancel", loan.pk)), "Cancel the loan")
        self.client.post(self.url("payroll_loan_cancel", loan.pk))
        loan.refresh_from_db()
        self.assertEqual(loan.status, Status.CANCELLED)

    def test_an_approved_loan_needs_a_reason_to_be_cancelled(self):
        loan = self.approved()
        self.assertContains(self.client.post(self.url("payroll_loan_cancel", loan.pk), {"reason": ""}), "This field is required")
        self.client.post(self.url("payroll_loan_cancel", loan.pk), {"reason": "Changed his mind"})
        loan.refresh_from_db()
        self.assertEqual((loan.status, loan.closed_note), (Status.CANCELLED, "Changed his mind"))

    def test_an_active_loan_offers_the_actions_and_they_work(self):
        loan = self.active()
        page = self.client.get(self.url("payroll_loan", loan.pk))
        for text in ("Record an early payment", "Pay off the loan", "Skip the next month", "Reschedule the rest",
                     "Waive the balance", "Mar 2026"):
            self.assertContains(page, text)
        self.client.post(self.url("payroll_loan_payment", loan.pk), {"amount": "250", "paid_on": "2026-03-05", "method": "bank",
                                                                     "reference": "TRF-9"})
        self.assertEqual(loan.balance, D("950.000"))
        self.client.post(self.url("payroll_loan_skip", loan.pk))
        self.assertEqual(loan.instalments.filter(status="skipped").count(), 1)
        self.client.post(self.url("payroll_loan_reschedule", loan.pk), {"months": "5", "month": "6", "year": "2026"})
        self.assertEqual(loan.instalments.filter(status="scheduled").count(), 5)
        self.client.post(self.url("payroll_loan_preclose", loan.pk), {"paid_on": "2026-03-06", "method": "cash"})
        loan.refresh_from_db()
        self.assertEqual((loan.status, loan.balance), (Status.CLOSED, D("0.000")))

    def test_a_payment_that_is_too_large_is_a_form_error(self):
        loan = self.active()
        page = self.client.post(self.url("payroll_loan_payment", loan.pk),
                                {"amount": "5000", "paid_on": "2026-03-05", "method": "bank"})
        self.assertContains(page, "balance is only")
        self.assertEqual(loan.payments.count(), 0)

    def test_waiving_asks_for_a_reason_and_closes_the_loan(self):
        loan = self.active()
        self.assertContains(self.client.post(self.url("payroll_loan_waive", loan.pk), {"reason": ""}), "This field is required")
        self.client.post(self.url("payroll_loan_waive", loan.pk), {"reason": "Written off by the owner"})
        loan.refresh_from_db()
        self.assertEqual((loan.status, loan.closed_note), (Status.CLOSED, "Waived: Written off by the owner"))

    def test_a_leaver_with_a_balance_is_called_out(self):
        loan = self.active()
        from apps.employees.models import Employee
        Employee.objects.filter(pk=self.ali.pk).update(status="separated")
        self.assertContains(self.client.get(self.url("payroll_loan", loan.pk)), "has left the company and 1200.000 BHD is still owed")

    def test_the_statement_lists_the_schedule_and_the_repayments(self):
        loan = self.active()
        ls.record_payment(self.hr, loan, D("100"), date(2026, 3, 5), "bank", "TRF-9")
        page = self.client.get(self.url("payroll_loan_statement", loan.pk))
        for text in ("Loan statement", loan.number, "Ali", "TRF-9", "1100.000", "Mar 2026"):
            self.assertContains(page, text)


class PayrollRunTests(LoanPageCase):
    def test_the_run_page_picks_up_the_instalment_and_the_loan_page_shows_it_deducted(self):
        loan = self.active()
        run = self.draft_run()
        self.assertContains(self.client.get(self.url("payroll_run", run.pk)), "Pick up charges (1)")
        self.client.post(self.url("payroll_run_charges", run.pk))
        self.assertContains(self.client.get(self.url("payroll_run", run.pk)), f"Loan {loan.number}, instalment 1 of 12")
        run = self.approve(run)
        page = self.client.get(self.url("payroll_loan", loan.pk))
        self.assertContains(page, "Deducted")
        self.assertContains(page, "Deducted in payroll")
        self.assertContains(page, "1100.000")

    def test_the_manual_adjustment_form_does_not_offer_loans(self):
        run = self.draft_run()
        categories = dict(self.client.get(self.url("payroll_adjustment_add", run.pk)).context["form"].fields["category"].choices)
        self.assertNotIn("loan", categories)
