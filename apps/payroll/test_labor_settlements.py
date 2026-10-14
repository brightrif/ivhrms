"""Final settlements for direct labor, built on the real labor module (workers, pay terms, hours, overtime claims)."""
from datetime import date
from decimal import Decimal

from apps.attendance.models import Attendance
from apps.audit.models import AuditEvent
from apps.core import approvals
from apps.employees.models import Employee
from apps.labor import services as labor_services
from apps.labor.services import LaborError
from apps.labor.timesheet import TimeEntry
from apps.leave.models import LeaveLedger, LeaveType

from . import runs, settlement_rules as rules, settlements
from .services import PayrollError
from .test_labor_runs import WORKING, LaborCase
from .test_settlements import LWD, Status, approval_of

D = Decimal


class LaborSettleCase(LaborCase):
    def setUp(self):
        super().setUp()
        self.annual = LeaveType.objects.create(company=self.co, code="AL", name="Annual leave", annual_entitlement=D("30"),
                                               tracks_balance=True, is_paid=True)

    def leaver(self, name, *, basis, rate, days=WORKING, hours=True, **kw):
        emp = self.worker(name, basis=basis, rate=rate, **kw)
        for year in (2025, 2026):
            LeaveLedger.objects.create(employee=emp, leave_type=self.annual, year=year, kind="entitlement", days=D("30"))
        self.attend(emp, days)
        if hours:
            self.full_month_hours(emp, days)
        return emp

    def settle(self, emp, **kw):
        args = dict(reason="resignation", last_working_day=LWD, notice_date=date(2026, 3, 1), user=self.hr)
        args.update(kw)
        return settlements.create_settlement(emp, **args)

    def kinds(self, case):
        return {i.kind: i.amount for i in case.items.all()}

    def codes(self, case, level=None):
        return sorted(i["code"] for i in case.issues if level in (None, i["level"]))


class CalculationTests(LaborSettleCase):
    def test_a_daily_worker_gets_the_last_months_wages_overtime_leave_and_gratuity(self):
        ravi = self.leaver("Ravi", basis="daily", rate="5")
        TimeEntry.objects.filter(employee=ravi, date=date(2026, 3, 2)).update(hours=D("10"))
        self.claims()
        case = self.settle(ravi)
        labels = {i.label: i.amount for i in case.items.all()}
        self.assertEqual(self.codes(case, "block"), [])
        self.assertEqual(labels["Wages 01 Mar to 31 Mar 2026"], D("135.000"))
        self.assertEqual(labels["Approved overtime and premium (2.00 hours)"], D("1.563"))
        self.assertEqual(self.kinds(case)["leave"], D("187.500"))                 # 37.5 days at 150 / 30
        gratuity = rules.gratuity(date(2020, 1, 1), LWD, D("150"))["employer"]
        self.assertEqual((self.kinds(case)["gratuity"], gratuity), (D("400.000"), D("400.000")))
        self.assertEqual(case.total, D("724.063"))
        self.assertEqual(case.details["basis"]["monthly_total"], "150.000")
        self.assertIn("daily rate 5.000 x 30 days", case.details["basis_note"])

    def test_an_hourly_worker_is_valued_at_rate_times_standard_hours_times_30(self):
        hamid = self.leaver("Hamid", basis="hourly", rate="1.25")
        case = self.settle(hamid)
        self.assertEqual(case.details["basis"]["monthly_total"], "300.000")
        self.assertEqual(case.details["daily_rate"], "10.000")
        labels = {i.label: i.amount for i in case.items.all()}
        self.assertEqual(labels["Wages 01 Mar to 31 Mar 2026"], D("270.000"))     # 216 hours x 1.25
        self.assertEqual((self.kinds(case)["leave"], self.kinds(case)["gratuity"]), (D("375.000"), D("800.000")))
        self.assertEqual(case.total, D("1445.000"))

    def test_a_monthly_worker_leaving_mid_month_is_paid_the_days_worked_of_the_whole_month(self):
        saleh = self.leaver("Saleh", basis="monthly", rate="300", ot=False, days=[d for d in WORKING if d.day <= 15],
                            hours=False)
        case = self.settle(saleh, last_working_day=date(2026, 3, 15), notice_date=date(2026, 2, 13))
        labels = {i.label: i.amount for i in case.items.all()}
        self.assertEqual(labels["Wages 01 Mar to 15 Mar 2026"], D("145.161"))      # 15 days of 300 / 31
        self.assertEqual(self.kinds(case)["leave"], D("360.000"))                  # 60 - 30 + 6 earned = 36 days x 10
        self.assertEqual(self.kinds(case)["gratuity"], D("800.000"))
        self.assertEqual(self.codes(case, "block"), [])

    def test_a_bahraini_pays_sio_on_the_wages_and_gets_no_employer_gratuity(self):
        ali = self.leaver("Ali", basis="daily", rate="5", nationality="Bahraini")
        case = self.settle(ali)
        self.assertEqual(self.kinds(case)["sio"], D("-6.750"))                    # 5% of the 135 earned
        self.assertNotIn("gratuity", self.kinds(case))
        self.assertFalse(case.details["gratuity"]["applies"])

    def test_the_labor_payroll_blocks_stop_a_settlement_too(self):
        ravi = self.leaver("Ravi", basis="daily", rate="5")
        TimeEntry.objects.filter(employee=ravi, date=date(2026, 3, 4)).update(status="draft")
        case = self.settle(ravi)
        self.assertIn("hours_draft", self.codes(case, "block"))
        with self.assertRaisesMessage(PayrollError, "problem to fix first"):
            settlements.submit_settlement(case, self.hr)

    def test_overtime_not_yet_claimed_blocks_until_it_is_prepared(self):
        ravi = self.leaver("Ravi", basis="daily", rate="5")
        TimeEntry.objects.filter(employee=ravi, date=date(2026, 3, 2)).update(hours=D("10"))
        case = self.settle(ravi)
        self.assertIn("overtime_unprepared", self.codes(case, "block"))
        self.claims()
        self.assertEqual(self.codes(settlements.calculate_settlement(case), "block"), [])

    def test_no_pay_terms_means_no_dues_can_be_worked_out(self):
        ravi = self.leaver("Ravi", basis="daily", rate="5")
        from apps.labor.models import LaborRate
        LaborRate.objects.filter(profile__employee=ravi).update(effective_from=date(2026, 3, 10))
        case = self.settle(ravi)
        self.assertIn("no_terms", self.codes(case, "block"))


class RunIntegrationTests(LaborSettleCase):
    def test_a_settled_worker_is_left_out_of_the_labor_runs_that_it_pays(self):
        ravi = self.leaver("Ravi", basis="daily", rate="5", hours=False, ot=False)
        keep = self.leaver("Keep", basis="daily", rate="5", hours=False, ot=False)
        case = self.settle(ravi)
        self.assertEqual(set(self.labor_run().lines.values_list("employee_name", flat=True)), {"Keep"})
        settlements.cancel_settlement(case)
        runs.cancel_run(PayrollRunFor.latest())
        self.assertEqual(set(self.labor_run().lines.values_list("employee_name", flat=True)), {"Keep", "Ravi"})
        self.assertTrue(keep.pk)

    def test_a_labor_run_made_before_cannot_be_sent_with_the_settled_worker_in_it(self):
        ravi = self.leaver("Ravi", basis="daily", rate="5", hours=False, ot=False)
        run = self.labor_run()
        self.assertEqual(run.lines.count(), 1)
        self.settle(ravi)
        with self.assertRaisesMessage(PayrollError, "final settlement that pays this month"):
            runs.submit_run(run, self.hr)

    def test_a_labor_line_shows_the_gratuity_contribution_for_a_non_bahraini(self):
        ravi = self.leaver("Ravi", basis="daily", rate="5", hours=False, ot=False)
        ali = self.leaver("Ali", basis="daily", rate="5", hours=False, ot=False, nationality="Bahraini")
        omar = self.leaver("Omar", basis="daily", rate="5", hours=False, ot=False, nationality="Omani")
        run = self.labor_run()
        self.assertEqual(run.lines.get(employee=ravi).breakdown["eosg"], {"rate": "8.4", "wage": "135.000", "amount": "11.340"})
        self.assertIsNone(run.lines.get(employee=ali).breakdown["eosg"])
        self.assertIsNone(run.lines.get(employee=omar).breakdown["eosg"])
        self.assertEqual(runs.totals(run)["eosg"], D("11.340"))


class PayrollRunFor:
    @staticmethod
    def latest():
        from .payrun import PayrollRun
        return PayrollRun.objects.filter(kind="labor").latest("pk")


class ApprovalAndLockTests(LaborSettleCase):
    def approved(self, emp):
        case = self.settle(emp)
        settlements.submit_settlement(case, self.hr)
        approvals.decide(approval_of(case).pk, self.boss, "approve")
        case.refresh_from_db()
        return case

    def test_approval_separates_the_worker_and_the_month_is_closed_for_labor(self):
        from apps.labor import overtime_services as ot
        ravi = self.leaver("Ravi", basis="daily", rate="5")                      # overtime yes: hours are recorded
        case = self.approved(ravi)
        ravi.refresh_from_db()
        self.assertEqual((case.status, ravi.status), (Status.APPROVED, "separated"))
        late = self.hours(ravi, date(2026, 3, 13), "11")                          # a late entry, three hours over
        self.assertEqual(late.status, "confirmed")
        with self.assertRaisesMessage(LaborError, "Payroll for March 2026 (final settlement) has already been approved"):
            ot.prepare_claims(self.hr, self.project, self.site, date(2026, 3, 1), date(2026, 3, 31))

    def test_a_draft_settlement_closes_nothing_and_neither_does_another_workers(self):
        ravi = self.leaver("Ravi", basis="daily", rate="5", hours=False, ot=False)
        other = self.leaver("Other", basis="daily", rate="5", hours=False, ot=False)
        self.settle(ravi)
        labor_services.change_rate(ravi.labor_profile, effective_from=date(2026, 3, 20), wage_basis="daily", rate=D("6"),
                                   standard_hours=D("8"), overtime_eligible=False)
        self.approved(other)
        labor_services.change_rate(ravi.labor_profile, effective_from=date(2026, 3, 25), wage_basis="daily", rate=D("7"),
                                   standard_hours=D("8"), overtime_eligible=False)

    def test_the_lock_registry_sees_a_paid_settlement_too(self):
        from .locks import labor_period_closed
        ravi = self.leaver("Ravi", basis="daily", rate="5", hours=False, ot=False)
        case = self.approved(ravi)
        self.assertEqual(labor_period_closed([ravi.pk], date(2026, 3, 1), date(2026, 3, 31)), "March 2026 (final settlement)")
        self.assertEqual(labor_period_closed([ravi.pk], date(2026, 4, 1), None), None)
        self.assertEqual(labor_period_closed([ravi.pk], date(2026, 1, 1), date(2026, 2, 28)), None)
        settlements.mark_paid(case, date(2026, 4, 2))
        self.assertEqual(labor_period_closed([ravi.pk], date(2026, 3, 15), date(2026, 3, 15)), "March 2026 (final settlement)")

    def test_the_figures_are_hidden_in_the_audit_log(self):
        ravi = self.leaver("Ravi", basis="daily", rate="5", hours=False, ot=False)
        self.settle(ravi)
        text = str([e.changes for e in AuditEvent.objects.filter(module="payroll", content_type__model="settlementitem")])
        for secret in ("135.000", "187.500", "400.000"):
            self.assertNotIn(secret, text)
