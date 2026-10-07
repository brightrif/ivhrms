from datetime import date, timedelta
from decimal import Decimal as D

from django.core import mail

from apps.compliance.services import add_months

from . import financing, services
from .loans import LoanAlertLog, LoanInstallment, VehicleLoan
from .models import Vehicle
from .test_upkeep import UpkeepCase


class LoanCase(UpkeepCase):
    def loan(self, v=None, **over):
        data = dict(lender="Bank of Bahrain", financed_amount=D("5000"), installment_count=12,
                    installment_amount=D("450"), first_due_on=self.today + timedelta(days=20))
        data.update(over)
        return financing.create_loan(v or self.vehicle(), **data)

    def pay_all(self, loan, upto=None):
        for inst in loan.installments.filter(status=LoanInstallment.Status.DUE).order_by("number")[:upto]:
            financing.pay_installment(inst, self.today, inst.amount)


class ScheduleTests(LoanCase):
    def test_dates_are_counted_from_the_first_one_so_month_ends_recover(self):
        rows = financing.build_schedule(date(2027, 1, 31), 4, D("100"))
        self.assertEqual([r[1] for r in rows], [date(2027, 1, 31), date(2027, 2, 28), date(2027, 3, 31), date(2027, 4, 30)])

    def test_the_last_installment_can_differ(self):
        rows = financing.build_schedule(date(2027, 1, 10), 3, D("100"), D("75"))
        self.assertEqual([r[2] for r in rows], [D("100"), D("100"), D("75")])

    def test_a_new_loan_builds_its_schedule_and_marks_the_vehicle_as_financed(self):
        v = self.vehicle()
        loan = self.loan(v)
        self.assertEqual(loan.installments.count(), 12)
        self.assertEqual(list(loan.installments.values_list("number", flat=True)), list(range(1, 13)))
        self.assertEqual((loan.scheduled_total, loan.cost_of_finance), (D("5400"), D("400")))
        v.refresh_from_db()
        self.assertEqual(v.ownership, Vehicle.Ownership.LOAN)
        small = self.loan(self.vehicle("2"), financed_amount=D("200"), installment_count=3,
                          installment_amount=D("100"), final_installment_amount=D("75"))
        self.assertEqual(small.scheduled_total, D("275"))
        self.assertEqual(small.purchase_price, D("200"))

    def test_loan_rules(self):
        v = self.vehicle()
        for bad in (dict(installment_count=0), dict(installment_count=121), dict(lender="  "),
                    dict(financed_amount=D("0")), dict(installment_amount=D("0")), dict(already_paid=13),
                    dict(installment_count=10, installment_amount=D("400")),          # 4,000 repays less than the 5,000 financed
                    dict(already_paid=1)):                                           # the first one is not due yet
            with self.assertRaises(services.VehicleError, msg=bad):
                self.loan(v, **bad)
        self.assertFalse(VehicleLoan.objects.exists())
        self.loan(v)
        with self.assertRaises(services.VehicleError):                                # one active loan per vehicle
            self.loan(v)

    def test_a_leased_or_sold_vehicle_cannot_take_a_loan(self):
        leased, sold = self.vehicle("1", ownership=Vehicle.Ownership.LEASED), self.vehicle("2")
        services.mark_sold(sold, self.today)
        for v in (leased, sold):
            with self.assertRaises(services.VehicleError):
                self.loan(v)


class OnboardingTests(LoanCase):
    def test_installments_already_paid_are_marked_so(self):
        loan = self.loan(first_due_on=add_months(self.today, -4), already_paid=3)
        paid = list(loan.installments.filter(status="paid").order_by("number"))
        self.assertEqual([p.number for p in paid], [1, 2, 3])
        self.assertTrue(all(p.paid_on == p.due_on and p.paid_amount == p.amount for p in paid))
        self.assertEqual((financing.loan_summary(loan).paid_count, loan.status), (3, VehicleLoan.Status.ACTIVE))

    def test_a_loan_with_everything_paid_is_complete(self):
        loan = self.loan(first_due_on=add_months(self.today, -11), already_paid=12)
        self.assertEqual(loan.status, VehicleLoan.Status.COMPLETED)


class PaymentTests(LoanCase):
    def test_paying_in_any_order_and_completing_the_loan(self):
        loan = self.loan(installment_count=3, financed_amount=D("1000"), installment_amount=D("400"))
        third = loan.installments.get(number=3)
        paid = financing.pay_installment(third, self.today, D("405.000"), " TT-1 ")      # an extra late fee is allowed
        self.assertEqual((paid.status, paid.paid_amount, paid.reference), ("paid", D("405.000"), "TT-1"))
        with self.assertRaises(services.VehicleError):
            financing.pay_installment(third, self.today, D("400"))                       # only once
        self.pay_all(loan)
        loan.refresh_from_db()
        self.assertEqual(loan.status, VehicleLoan.Status.COMPLETED)
        with self.assertRaises(services.VehicleError):
            financing.pay_installment(loan.installments.get(number=1), self.today, D("400"))

    def test_payment_rules(self):
        loan = self.loan()
        first = loan.installments.get(number=1)
        with self.assertRaises(services.VehicleError):
            financing.pay_installment(first, self.today + timedelta(days=1), D("450"))
        with self.assertRaises(services.VehicleError):
            financing.pay_installment(first, self.today, D("0"))

    def test_undoing_a_payment(self):
        loan = self.loan(installment_count=2, financed_amount=D("800"), installment_amount=D("400"))
        self.pay_all(loan)
        first = loan.installments.get(number=1)
        with self.assertRaises(services.VehicleError):
            financing.reverse_payment(first, " ")                                        # a reason is needed
        financing.reverse_payment(first, "wrong installment")
        first.refresh_from_db()
        loan.refresh_from_db()
        self.assertEqual((first.status, first.paid_on, first.paid_amount, loan.status), ("due", None, None, "active"))
        with self.assertRaises(services.VehicleError):
            financing.reverse_payment(first, "again")                                    # nothing to undo


class SummaryTests(LoanCase):
    def test_figures(self):
        loan = self.loan(first_due_on=self.today - timedelta(days=75))                   # three installments are overdue
        s = financing.loan_summary(loan)
        self.assertEqual((s.left_count, s.overdue_count, s.outstanding, s.overdue_total, s.paid_total),
                         (12, 3, D("5400"), D("1350"), D("0")))
        self.assertEqual(s.next_due.number, 1)
        financing.pay_installment(loan.installments.get(number=1), self.today, D("450"))
        s = financing.loan_summary(loan)
        self.assertEqual((s.left_count, s.overdue_count, s.outstanding, s.paid_total, s.next_due.number),
                         (11, 2, D("4950"), D("450"), 2))

    def test_the_overview_lists_active_loans_for_those_who_may_see_them(self):
        self.loan(self.vehicle("1"))
        self.loan(self.vehicle("2"), installment_amount=D("500"), financed_amount=D("5000"))
        data = financing.overview(self.finance, self.today)
        self.assertEqual((len(data["loans"]), data["outstanding"], data["monthly"]), (2, D("11400"), D("950")))
        self.assertEqual(financing.overview(self.other_hr, self.today)["loans"], [])


class SettlementAndCancelTests(LoanCase):
    def test_early_settlement_clears_what_is_left_and_closes_the_loan(self):
        loan = self.loan()
        self.pay_all(loan, upto=2)
        with self.assertRaises(services.VehicleError):
            financing.settle_early(loan, self.today, D("0"))
        with self.assertRaises(services.VehicleError):
            financing.settle_early(loan, self.today + timedelta(days=1), D("3000"))
        settled = financing.settle_early(loan, self.today, D("3000"), " REF-2 ")
        self.assertEqual((settled.status, settled.settlement_amount, settled.settlement_reference),
                         (VehicleLoan.Status.SETTLED, D("3000"), "REF-2"))
        self.assertEqual(loan.installments.filter(status="settled").count(), 10)
        self.assertEqual(financing.loan_summary(loan).outstanding, D("0"))
        for action in (lambda: financing.settle_early(loan, self.today, D("1")),
                       lambda: financing.pay_installment(loan.installments.get(number=1), self.today, D("1")),
                       lambda: financing.reverse_payment(loan.installments.get(number=1), "oops")):
            with self.assertRaises(services.VehicleError):
                action()

    def test_a_vehicle_with_an_active_loan_cannot_be_sold_until_it_is_settled(self):
        v = self.vehicle()
        loan = self.loan(v)
        with self.assertRaises(services.VehicleError) as ctx:
            services.mark_sold(v, self.today)
        self.assertIn("active bank loan", str(ctx.exception))
        financing.settle_early(loan, self.today, D("5000"))
        self.assertEqual(services.mark_sold(v, self.today).status, Vehicle.Status.SOLD)

    def test_cancelling_a_loan_entered_by_mistake(self):
        v = self.vehicle()
        loan = self.loan(v)
        with self.assertRaises(services.VehicleError):
            financing.void_loan(loan, "")
        self.pay_all(loan, upto=1)
        with self.assertRaises(services.VehicleError):                                   # a payment is recorded
            financing.void_loan(loan, "wrong vehicle")
        financing.reverse_payment(loan.installments.get(number=1), "wrong")
        financing.void_loan(loan, "wrong vehicle")
        self.assertEqual(financing.current_loan(v), None)
        self.assertTrue(self.loan(v).pk)                                                 # the vehicle can take its real loan


class AlertTests(LoanCase):
    def to(self):
        return sorted(m.to[0] for m in mail.outbox)

    def test_finance_is_told_a_few_days_before_and_only_once(self):
        self.loan(first_due_on=self.today + timedelta(days=3))
        stats = financing.run_loan_scan(self.today)
        self.assertEqual((stats["alerts"], stats["emails"]), (1, 1))
        self.assertEqual(self.to(), ["fin@example.com"])                                  # not HR, not Management yet
        self.assertIn("Due in 3 days", mail.outbox[0].subject)
        self.assertIn("450.000 BHD", mail.outbox[0].body)
        self.assertEqual(financing.run_loan_scan(self.today)["alerts"], 0)

    def test_an_overdue_installment_goes_to_management_too(self):
        self.loan(first_due_on=self.today - timedelta(days=2))
        stats = financing.run_loan_scan(self.today)
        self.assertEqual(stats["alerts"], 1)
        self.assertEqual(self.to(), ["boss@example.com", "fin@example.com"])
        self.assertIn("OVERDUE", mail.outbox[0].subject)
        self.assertEqual(financing.run_loan_scan(self.today)["alerts"], 0)

    def test_nothing_is_sent_for_distant_paid_or_settled_installments(self):
        far = self.loan(self.vehicle("1"), first_due_on=self.today + timedelta(days=30))
        paid = self.loan(self.vehicle("2"), first_due_on=self.today + timedelta(days=2))
        financing.pay_installment(paid.installments.get(number=1), self.today, D("450"))
        gone = self.loan(self.vehicle("3"), first_due_on=self.today + timedelta(days=2))
        financing.settle_early(gone, self.today, D("1000"))
        stats = financing.run_loan_scan(self.today)
        self.assertEqual((stats["alerts"], mail.outbox), (0, []))
        self.assertEqual(far.installments.count(), 12)

    def test_the_alert_is_logged_even_when_nobody_has_an_email(self):
        self.loan(first_due_on=self.today + timedelta(days=1))
        for user in (self.finance, self.boss):
            user.email = ""
            user.save()
        self.assertEqual(financing.run_loan_scan(self.today)["alerts"], 1)
        self.assertEqual(LoanAlertLog.objects.get().recipients, 0)
