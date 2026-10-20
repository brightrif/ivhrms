from datetime import date
from decimal import Decimal
from unittest.mock import patch

from apps.core import approvals
from apps.core.models import ApprovalFlow
from apps.employees.models import Employee

from . import loan_services as ls
from . import runs, settlements
from .finalsettlement import FinalSettlement
from .loans import Loan, LoanInstalment, LoanPayment
from .services import PayrollError
from .test_settlements import LWD, SettleCase, approval_of

D = Decimal
Status, Inst = Loan.Status, LoanInstalment.Status
FIRST = date(2026, 3, 1)                       # the month of the payroll run these tests use
NO_LIMITS = {"salaries": None, "months": None, "share": None}


class LoanCase(SettleCase):
    def loan(self, emp=None, amount="1200", months=12, first=FIRST, kind="loan", **kw):
        return ls.create_loan(self.hr, emp or self.ali, kind=kind, amount=D(amount), months=months, first_month=first, **kw)

    def approved(self, **kw):
        """Agreed verbally, so it starts approved."""
        kw.setdefault("verbal", True)
        kw.setdefault("agreed_by", "Mr. Owner")
        kw.setdefault("agreed_on", date(2026, 2, 15))
        return self.loan(**kw)

    def active(self, **kw):
        loan = self.approved(**kw)
        return ls.record_payout(self.hr, loan, date(2026, 2, 20), "TRF-1")

    def rows(self, loan, *statuses):
        qs = loan.instalments.all() if not statuses else loan.instalments.filter(status__in=statuses)
        return list(qs.order_by("due_month", "id"))


class CreatingTests(LoanCase):
    def test_a_draft_has_its_totals_and_no_schedule_yet(self):
        loan = self.loan()
        self.assertEqual((loan.status, loan.total, loan.balance, loan.interest_rate), (Status.DRAFT, D("1200"), D("1200"), 0))
        self.assertFalse(loan.instalments.exists())
        self.assertEqual(loan.number, f"LN-{loan.pk:04d}")
        self.assertEqual((loan.company_id, loan.prepared_by), (self.co.pk, self.hr))

    def test_interest_is_flat_and_spread_equally(self):
        loan = self.approved(amount="1000", interest_rate=D("5"))
        self.assertEqual((loan.interest_total, loan.total), (D("50.000"), D("1050.000")))
        self.assertEqual([r.amount for r in self.rows(loan)], [D("87.500")] * 12)

    def test_the_last_instalment_takes_the_rounding(self):
        loan = self.approved(amount="1000")
        rows = self.rows(loan)
        self.assertEqual((rows[0].amount, rows[-1].amount), (D("83.333"), D("83.337")))
        self.assertEqual(sum(r.amount for r in rows), D("1000.000"))
        self.assertEqual([r.due_month for r in (rows[0], rows[-1])], [date(2026, 3, 1), date(2027, 2, 1)])

    def test_a_loan_agreed_verbally_starts_approved_with_who_and_when(self):
        loan = self.approved()
        self.assertEqual((loan.status, loan.agreed_by, loan.agreed_on), (Status.APPROVED, "Mr. Owner", date(2026, 2, 15)))
        self.assertTrue(loan.verbal)
        self.assertEqual(loan.instalments.count(), 12)
        self.assertIsNotNone(loan.approved_at)

    def test_verbal_needs_who_and_when_and_not_in_the_future(self):
        for who, when, text in (("", date(2026, 2, 15), "who agreed"), ("Mr. Owner", None, "who agreed"),
                                ("Mr. Owner", date(2099, 1, 1), "future")):
            with self.assertRaisesMessage(PayrollError, text):
                self.loan(verbal=True, agreed_by=who, agreed_on=when)
        self.assertFalse(Loan.objects.exists())

    def test_a_salary_advance_is_one_instalment(self):
        loan = self.approved(kind="advance", amount="150", months=6)
        self.assertEqual((loan.months, loan.instalments.count()), (1, 1))

    def test_bad_values_are_refused_in_plain_words(self):
        for kwargs, text in (({"amount": "0"}, "more than zero"), ({"amount": "10.0005"}, "three decimal places"),
                             ({"months": 0}, "between 1 and 120"), ({"months": 121}, "between 1 and 120"),
                             ({"first": date(2019, 12, 1)}, "before the month the person joined"),
                             ({"interest_rate": D("101")}, "between 0 and 100")):
            with self.assertRaisesMessage(PayrollError, text):
                self.loan(**kwargs)
        self.assertFalse(Loan.objects.exists())

    def test_nothing_for_someone_who_left_or_has_a_final_settlement(self):
        Employee.objects.filter(pk=self.sara.pk).update(status="separated")
        with self.assertRaisesMessage(PayrollError, "has left the company"):
            self.loan(emp=self.sara)
        FinalSettlement.objects.create(employee=self.ali, reason="resignation", last_working_day=LWD)
        with self.assertRaisesMessage(PayrollError, "has a final settlement"):
            self.loan()

    def test_a_draft_can_be_changed_and_cancelled_but_nothing_later(self):
        loan = ls.update_draft(self.hr, self.loan(), kind="loan", amount=D("900"), months=9, first_month=FIRST, reason="Car")
        self.assertEqual((loan.amount, loan.months, loan.reason), (D("900"), 9, "Car"))
        ls.cancel_loan(self.hr, loan)
        self.assertEqual(Loan.objects.get(pk=loan.pk).status, Status.CANCELLED)
        approved = self.approved()
        with self.assertRaisesMessage(PayrollError, "cannot be changed"):
            ls.update_draft(self.hr, approved, kind="loan", amount=D("1"), months=1, first_month=FIRST)
        with self.assertRaisesMessage(PayrollError, "Give the reason"):
            ls.cancel_loan(self.hr, approved)
        ls.cancel_loan(self.hr, approved, "Changed his mind")
        with self.assertRaisesMessage(PayrollError, "cannot be cancelled"):
            ls.cancel_loan(self.hr, self.active())


class LimitTests(LoanCase):
    def limits(self, **over):
        return patch("apps.payroll.loan_services.loan_settings.limits_for", return_value={**NO_LIMITS, **over})

    def test_no_limits_means_no_warnings(self):
        with self.limits():
            self.assertEqual(ls.limit_warnings(self.ali, D("99999"), 120), [])

    def test_each_limit_warns_in_plain_words(self):
        with self.limits(months=6):
            self.assertIn("more than the 6 allowed", ls.limit_warnings(self.ali, D("100"), 12)[0])
        with self.limits(salaries=D("1")):                                  # Ali earns 900
            self.assertIn("monthly salaries", ls.limit_warnings(self.ali, D("1000"), 12)[0])
            self.assertEqual(ls.limit_warnings(self.ali, D("900"), 12), [])
        with self.limits(share=10):                                         # 90 a month at most
            self.assertIn("10% of the monthly pay", ls.limit_warnings(self.ali, D("1200"), 12)[0])
            self.assertEqual(ls.limit_warnings(self.ali, D("1000"), 12), [])

    def test_passing_a_limit_needs_a_reason_which_is_kept(self):
        with self.limits(months=6):
            with self.assertRaises(ls.LimitError) as caught:
                self.loan()
            self.assertIn("Give the reason to go ahead", str(caught.exception))
            self.assertEqual(len(caught.exception.warnings), 1)
            self.assertFalse(Loan.objects.exists())
            loan = self.loan(override_reason="Agreed by the owner")
        self.assertEqual(loan.override_reason, "Agreed by the owner")


class ApprovalTests(LoanCase):
    def test_the_approval_flow_exists_and_management_decides(self):
        flow = ApprovalFlow.objects.get(code="payroll.loan")
        self.assertEqual((flow.steps.get().group, flow.name), (self.management, "Loan approval"))

    def test_approval_makes_the_schedule(self):
        loan = self.loan()
        ls.submit_loan(loan, self.hr)
        self.assertEqual(Loan.objects.get(pk=loan.pk).status, Status.SUBMITTED)
        with self.assertRaisesMessage(PayrollError, "waiting for approval"):
            ls.update_draft(self.hr, loan, kind="loan", amount=D("1"), months=1, first_month=FIRST)
        with self.assertRaises(approvals.ApprovalError):
            approvals.decide(approval_of(loan).pk, self.hr, "approve")           # the sender cannot approve it
        approvals.decide(approval_of(loan).pk, self.boss, "approve")
        loan = Loan.objects.get(pk=loan.pk)
        self.assertEqual((loan.status, loan.instalments.count()), (Status.APPROVED, 12))
        self.assertIsNotNone(loan.approved_at)

    def test_a_rejected_loan_returns_to_draft_with_the_reason(self):
        loan = self.loan()
        ls.submit_loan(loan, self.hr)
        approvals.decide(approval_of(loan).pk, self.boss, "reject", comment="Too large this month")
        loan = Loan.objects.get(pk=loan.pk)
        self.assertEqual((loan.status, loan.note), (Status.DRAFT, "Rejected: Too large this month"))
        self.assertFalse(loan.instalments.exists())

    def test_taking_it_back(self):
        loan = self.loan()
        ls.submit_loan(loan, self.hr)
        back = ls.withdraw_loan(loan, self.hr)
        self.assertEqual((back.status, back.note), (Status.DRAFT, "Taken back for changes."))

    def test_the_bank_transfer_is_recorded_with_a_reference_and_a_real_date(self):
        loan = self.approved()
        for paid_on, reference, text in ((date(2026, 2, 20), " ", "bank transfer reference"),
                                         (date(2099, 1, 1), "TRF-1", "future")):
            with self.assertRaisesMessage(PayrollError, text):
                ls.record_payout(self.hr, loan, paid_on, reference)
        loan = ls.record_payout(self.hr, loan, date(2026, 2, 20), "TRF-1")
        self.assertEqual((loan.status, loan.paid_out_on, loan.payout_reference), (Status.ACTIVE, date(2026, 2, 20), "TRF-1"))
        with self.assertRaisesMessage(PayrollError, "cannot be paid out"):
            ls.record_payout(self.hr, loan, date(2026, 2, 21), "TRF-2")


class PayrollTests(LoanCase):
    def candidates(self, run):
        return [c for c in runs.charge_candidates(run, self.hr) if c[0] == "loan"]

    def test_an_instalment_is_picked_up_with_the_other_charges(self):
        loan = self.active(amount="1000")
        run = self.new_run()
        found = self.candidates(run)
        self.assertEqual(len(found), 1)
        source, source_id, emp_id, category, label, amount = found[0]
        self.assertEqual((emp_id, category, amount), (self.ali.pk, "loan", D("83.333")))
        self.assertEqual(label, f"Loan {loan.number}, instalment 1 of 12")
        before = self.line(run, self.ali).net
        self.assertEqual(runs.import_charges(run, self.hr), 1)
        adjustment = run.adjustments.get(source="loan")
        self.assertEqual((adjustment.kind, adjustment.category, adjustment.amount), ("deduction", "loan", D("83.333")))
        self.assertEqual(self.line(run, self.ali).net, before - D("83.333"))
        self.assertEqual(runs.import_charges(run, self.hr), 0)                      # never twice

    def test_only_a_paid_out_loan_is_picked_up(self):
        self.approved()
        self.assertEqual(self.candidates(self.new_run()), [])

    def test_a_later_month_waits(self):
        self.active(first=date(2026, 5, 1))
        self.assertEqual(self.candidates(self.new_run()), [])

    def test_approving_the_run_counts_the_instalment_as_repaid(self):
        loan = self.active(amount="1000")
        run = self.new_run()
        runs.import_charges(run, self.hr)
        self.assertEqual(loan.balance, D("1000"))                                  # picking up repays nothing yet
        run = self.approve(run)
        run.refresh_from_db()
        row = self.rows(loan)[0]
        self.assertEqual((row.status, row.run_id, row.deducted_on), (Inst.DEDUCTED, run.pk, date(2026, 3, 31)))
        payment = loan.payments.get()
        self.assertEqual((payment.method, payment.amount, payment.instalment_id), ("payroll", D("83.333"), row.pk))
        self.assertEqual(Loan.objects.get(pk=loan.pk).balance, D("916.667"))
        self.assertEqual(run.note, "")

    def test_a_cancelled_run_changes_nothing_and_the_instalment_is_offered_again(self):
        loan = self.active()
        run = self.new_run()
        runs.import_charges(run, self.hr)
        runs.cancel_run(run)
        self.assertEqual(self.rows(loan)[0].status, Inst.SCHEDULED)
        self.assertEqual(len(self.candidates(self.new_run())), 1)

    def test_removing_the_deduction_from_a_draft_run_offers_it_again(self):
        self.active()
        run = self.new_run()
        runs.import_charges(run, self.hr)
        self.assertEqual(self.candidates(run), [])
        runs.remove_adjustment(run.adjustments.get(source="loan"))
        self.assertEqual(len(self.candidates(run)), 1)

    def test_the_last_instalment_closes_the_loan(self):
        loan = self.active(kind="advance", amount="150")
        run = self.new_run()
        runs.import_charges(run, self.hr)
        self.assertEqual(run.adjustments.get(source="loan").label, f"Salary advance {loan.number}")
        self.approve(run)
        loan = Loan.objects.get(pk=loan.pk)
        self.assertEqual((loan.status, loan.balance, loan.closed_note), (Status.CLOSED, D("0"), "Fully repaid."))
        self.assertEqual(loan.closed_on, date(2026, 3, 31))

    def test_an_instalment_that_changed_before_approval_is_flagged_not_counted(self):
        loan = self.active()
        run = self.new_run()
        runs.import_charges(run, self.hr)
        LoanInstalment.objects.filter(pk=self.rows(loan)[0].pk).update(status=Inst.SKIPPED)
        run = self.approve(run)
        run.refresh_from_db()
        self.assertIn("was skipped before this run was approved", run.note)
        self.assertEqual(loan.payments.count(), 0)

    def test_the_run_form_does_not_offer_a_loan_as_a_manual_reason(self):
        from apps.web.forms.payroll_runs import MANUAL_CATEGORIES
        self.assertNotIn("loan", dict(MANUAL_CATEGORIES))
        self.assertIn("advance", dict(MANUAL_CATEGORIES))


class RepayingTests(LoanCase):
    def test_an_early_payment_shortens_the_schedule_from_the_far_end(self):
        loan = self.active()                                                       # 12 x 100
        ls.record_payment(self.hr, loan, D("250"), date(2026, 3, 5), "bank", "TRF-9")
        rows = self.rows(loan)
        self.assertEqual([r.status for r in rows[-2:]], [Inst.COVERED, Inst.COVERED])
        self.assertEqual((rows[-3].status, rows[-3].amount, rows[-3].original_amount), (Inst.SCHEDULED, D("50.000"), D("100.000")))
        self.assertEqual(Loan.objects.get(pk=loan.pk).balance, D("950.000"))
        self.assertEqual(loan.payments.get().method, "bank")

    def test_paying_off_the_balance_closes_the_loan(self):
        loan = self.active()
        ls.pre_close(self.hr, loan, date(2026, 3, 5), "cash")
        loan = Loan.objects.get(pk=loan.pk)
        self.assertEqual((loan.status, loan.balance, loan.closed_on), (Status.CLOSED, D("0.000"), date(2026, 3, 5)))
        self.assertFalse(loan.instalments.filter(status=Inst.SCHEDULED).exists())

    def test_a_payment_must_make_sense(self):
        loan = self.active()
        for args, text in (((D("5000"), date(2026, 3, 5), "bank"), "balance is only"),
                           ((D("0"), date(2026, 3, 5), "bank"), "more than zero"),
                           ((D("10"), date(2099, 1, 1), "bank"), "future"),
                           ((D("10"), date(2026, 2, 1), "bank"), "before the payout"),
                           ((D("10"), date(2026, 3, 5), "payroll"), "bank transfer or cash")):
            with self.assertRaisesMessage(PayrollError, text):
                ls.record_payment(self.hr, loan, *args)
        with self.assertRaisesMessage(PayrollError, "cannot be repaid"):
            ls.record_payment(self.hr, self.approved(), D("10"), date(2026, 3, 5), "bank")

    def test_skipping_moves_the_instalment_to_the_end(self):
        loan = self.active()
        ls.skip_next(self.hr, loan)
        rows = self.rows(loan)
        self.assertEqual(rows[0].status, Inst.SKIPPED)
        self.assertEqual((rows[-1].due_month, rows[-1].amount, rows[-1].status), (date(2027, 3, 1), D("100.000"), Inst.SCHEDULED))
        self.assertEqual(Loan.objects.get(pk=loan.pk).balance, D("1200"))

    def test_an_instalment_waiting_in_a_run_cannot_be_skipped_or_rescheduled(self):
        loan = self.active()
        run = self.new_run()
        runs.import_charges(run, self.hr)
        with self.assertRaisesMessage(PayrollError, "already in the March 2026 payroll run"):
            ls.skip_next(self.hr, loan)
        with self.assertRaisesMessage(PayrollError, "already in the March 2026 payroll run"):
            ls.reschedule(self.hr, loan, 4, date(2026, 5, 1))

    def test_rescheduling_spreads_what_is_left(self):
        loan = self.active()
        ls.reschedule(self.hr, loan, 4, date(2026, 5, 1))
        self.assertEqual(len(self.rows(loan, Inst.REPLACED)), 12)
        new = self.rows(loan, Inst.SCHEDULED)
        self.assertEqual([(r.due_month.month, r.amount) for r in new], [(m, D("300.000")) for m in (5, 6, 7, 8)])
        with self.assertRaisesMessage(PayrollError, "between 1 and 120"):
            ls.reschedule(self.hr, loan, 0, date(2026, 5, 1))

    def test_waiving_needs_a_reason_and_closes_the_loan(self):
        loan = self.active()
        with self.assertRaisesMessage(PayrollError, "reason for waiving"):
            ls.waive(self.hr, loan, " ")
        ls.waive(self.hr, loan, "Owner wrote it off")
        loan = Loan.objects.get(pk=loan.pk)
        self.assertEqual((loan.status, loan.balance, loan.closed_note), (Status.CLOSED, D("0"), "Waived: Owner wrote it off"))
        self.assertEqual(loan.payments.get().method, "waived")


class SettlementTests(LoanCase):
    def test_the_balance_is_a_line_on_the_settlement(self):
        loan = self.active(amount="1000")
        case = self.settle()
        item = case.items.get(kind="loan")
        self.assertEqual((item.amount, item.is_system, item.source, item.source_id), (D("-1000.000"), True, "loanbal", loan.pk))
        self.assertEqual(item.label, f"Loan {loan.number} balance")
        self.assertEqual(case.total, D("3125.000"))                                # 4125 less the loan
        self.assertEqual(self.codes(case, "warn"), [])

    def test_calculating_again_does_not_double_the_line(self):
        self.active()
        case = self.settle()
        settlements.calculate_settlement(case)
        settlements.calculate_settlement(case)
        self.assertEqual(case.items.filter(kind="loan").count(), 1)

    def test_approval_counts_it_as_repaid_and_closes_the_loan(self):
        loan = self.active(amount="1000")
        case = self.settle()
        settlements.submit_settlement(case, self.hr)
        approvals.decide(approval_of(case).pk, self.boss, "approve")
        loan = Loan.objects.get(pk=loan.pk)
        self.assertEqual((loan.status, loan.balance, loan.closed_note), (Status.CLOSED, D("0.000"), "Fully repaid."))
        self.assertEqual(loan.payments.get().method, "settlement")
        self.assertEqual(loan.payments.get().paid_on, LWD)

    def test_a_balance_the_settlement_cannot_cover_stays_owed_and_is_shown(self):
        loan = self.active(amount="9600", months=12)
        case = self.settle()
        self.assertEqual(case.items.get(kind="loan").amount, D("-4125.000"))        # everything the settlement can pay
        self.assertEqual(case.total, D("0.000"))
        self.assertEqual(self.codes(case, "warn"), ["loan_owed"])
        message = next(i["text"] for i in case.issues if i["code"] == "loan_owed")
        self.assertIn("5,475.000 BHD is still owed", message)
        settlements.submit_settlement(case, self.hr)
        approvals.decide(approval_of(case).pk, self.boss, "approve")
        loan = Loan.objects.get(pk=loan.pk)
        self.assertEqual((loan.status, loan.balance), (Status.ACTIVE, D("5475.000")))
        self.assertIn("5,475.000 BHD is still owed", loan.note)

    def test_a_waived_or_closed_loan_adds_no_line(self):
        loan = self.active()
        ls.waive(self.hr, loan, "Written off")
        self.assertFalse(self.settle().items.filter(kind="loan").exists())

    def test_two_loans_are_taken_oldest_first(self):
        first, second = self.active(amount="4000"), self.active(amount="500")
        case = self.settle()
        amounts = {i.source_id: i.amount for i in case.items.filter(kind="loan")}
        self.assertEqual(amounts, {first.pk: D("-4000.000"), second.pk: D("-125.000")})
