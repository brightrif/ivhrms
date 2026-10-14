"""Direct labor payroll, built on the real labor module: workers, pay terms, allocations, hours and overtime claims
are all made with labor's own services, so these tests also prove payroll reads them correctly.

March 2026 has 31 days and starts on a Sunday; Fridays (6, 13, 20, 27) are the weekly off, so there are 27 working days.
The SIO rates in the policy are made up for the tests."""
from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase

from apps.attendance.models import Attendance
from apps.employees.models import Employee
from apps.labor import deployment, overtime_services as ot, reporting
from apps.labor import services as labor_services
from apps.labor.models import LaborRate, Trade
from apps.labor.overtime import OvertimeClaim
from apps.labor.services import LaborError
from apps.labor.timesheet import TimeEntry
from apps.organization.models import Location, Project
from apps.scheduling.models import Holiday

from . import labor_payroll, runs, services
from .payrun import PayrollAdjustment, PayrollLine, PayrollRun
from .services import PayrollError
from .test_runs import RunCase, approvals_of
from .tests import VALID_IBAN, make_profile

D = Decimal
FIRST, LAST = date(2026, 3, 1), date(2026, 3, 31)
WORKING = [date(2026, 3, d) for d in range(1, 32) if date(2026, 3, d).weekday() != 4]
FRIDAY = date(2026, 3, 6)
MON2 = date(2026, 3, 2)


class LaborCase(RunCase):
    def setUp(self):
        super().setUp()
        ot.set_policy(self.co, effective_from=date(2020, 1, 1), working_day_multiplier=D("1.25"),
                      weekly_off_multiplier=D("1.5"), holiday_multiplier=D("1.5"), all_hours_on_days_off=True,
                      monthly_divisor=30)
        self.site = Location.objects.create(code="S1", name="Site One", is_site=True)
        self.project = Project.objects.create(company=self.co, code="P1", name="Tower", location=self.site)
        self.trade = Trade.objects.create(code="MASON", name="Mason")

    # --- building blocks
    def worker(self, name, *, basis, rate, ot=True, premium=False, holidays=False, nationality="Indian", pay="bank",
               joined=date(2020, 1, 1), engagement="direct"):
        emp = Employee(company=self.co, first_name=name, joining_date=joined, nationality=nationality)
        profile = labor_services.create_labor_worker(
            emp, engagement=engagement, trade=self.trade, wage_basis=basis, rate=D(rate), standard_hours=D("8"),
            overtime_eligible=ot, weekend_premium=premium, paid_holidays=holidays)
        emp = profile.employee
        deployment.allocate(emp, project=self.project, location=self.site, effective_from=max(joined, date(2026, 1, 1)))
        if pay == "bank":
            services.save_payment_details(emp, method="bank", bank_name="NBB", iban=VALID_IBAN)
        elif pay == "cash":
            services.save_payment_details(emp, method="cash")
        return emp

    def attend(self, emp, days=WORKING, status="present", skip=()):
        for d in days:
            if d not in skip:
                Attendance.objects.update_or_create(employee=emp, date=d, defaults={
                    "status": status, "project": self.project, "location": self.site})

    def hours(self, emp, day, hours="8", *, status="confirmed"):
        rate = emp.labor_profile.rates.first()
        return TimeEntry.objects.create(employee=emp, date=day, project=self.project, location=self.site,
                                        hours=D(hours), expected_hours=D("8"), overtime_eligible=rate.overtime_eligible,
                                        status=status)

    def full_month_hours(self, emp, days=WORKING, **special):
        for d in days:
            self.hours(emp, d, special.get(str(d.day), "8"))

    def claims(self, approve=True):
        ot.prepare_claims(self.hr, self.project, self.site, FIRST, LAST)
        if approve:
            ot.decide(self.boss, OvertimeClaim.objects.filter(status="pending"), True)

    def labor_run(self):
        return runs.create_run(self.co, 2026, 3, user=self.hr, kind="labor")

    def line(self, run, emp):
        return PayrollLine.objects.get(run=run, employee=emp)

    def codes(self, line, level=None):
        return sorted(i["code"] for i in line.issues if level in (None, i["level"]))


class WageRuleTests(LaborCase):
    def test_a_daily_worker_earns_the_daily_rate_for_each_day_worked_plus_approved_overtime(self):
        ravi = self.worker("Ravi", basis="daily", rate="5")
        self.attend(ravi)
        self.full_month_hours(ravi, **{"2": "10"})                  # two overtime hours on Monday 2 March
        self.claims()
        line = self.line(self.labor_run(), ravi)
        self.assertEqual(self.codes(line, "block"), [])
        self.assertEqual((line.gross, line.days_employed, line.absence_deduction), (D("136.563"), 27, D("0.000")))
        self.assertEqual((line.breakdown["wages"], line.breakdown["overtime"], line.breakdown["overtime_hours"]),
                         ("135.000", "1.563", "2.00"))                 # 2 h x 0.625 x 1.25

    def test_an_hourly_worker_is_paid_for_the_hours_and_the_friday_premium_through_the_claim(self):
        hamid = self.worker("Hamid", basis="hourly", rate="1.25", premium=True)
        self.attend(hamid, WORKING + [FRIDAY])
        self.full_month_hours(hamid, WORKING + [FRIDAY])
        self.claims()
        line = self.line(self.labor_run(), hamid)
        self.assertEqual(self.codes(line, "block"), [])
        self.assertEqual((line.breakdown["wages"], line.breakdown["overtime"]), ("270.000", "15.000"))   # 216 h x 1.25; 8 h x 1.25 x 1.5
        self.assertEqual(line.gross, D("285.000"))

    def test_a_monthly_salary_is_the_salary_for_a_full_month_even_a_31_day_one(self):
        saleh = self.worker("Saleh", basis="monthly", rate="300", ot=False)
        self.attend(saleh)
        line = self.line(self.labor_run(), saleh)
        self.assertEqual((line.gross, self.codes(line, "block")), (D("300.000"), []))

    def test_a_monthly_worker_loses_one_thirtieth_for_an_absent_day(self):
        saleh = self.worker("Saleh", basis="monthly", rate="300", ot=False)
        self.attend(saleh, skip=[date(2026, 3, 10)])
        self.attend(saleh, [date(2026, 3, 10)], status="absent")
        Attendance.objects.filter(employee=saleh, date=date(2026, 3, 11)).update(status="unpaid_leave")
        line = self.line(self.labor_run(), saleh)
        self.assertEqual(line.gross, D("280.000"))                      # 300 - 2 x 300/30
        self.assertEqual(line.unpaid_days, D("2"))

    def test_a_monthly_worker_who_joins_part_way_earns_the_days_employed(self):
        saleh = self.worker("Saleh", basis="monthly", rate="310", ot=False, joined=date(2026, 3, 21))
        self.attend(saleh, [d for d in WORKING if d >= date(2026, 3, 21)])
        line = self.line(self.labor_run(), saleh)
        self.assertEqual(line.gross, D("110.000"))                      # 11 days x 310/31

    def test_a_daily_worker_on_a_friday_with_the_premium_is_paid_through_the_claim_not_as_a_normal_day(self):
        ravi = self.worker("Ravi", basis="daily", rate="5", premium=True)
        days = WORKING[:5] + [FRIDAY]
        self.attend(ravi, days)
        for d in days:
            self.hours(ravi, d)
        self.claims()
        line = self.line(self.labor_run(), ravi)
        self.assertEqual((line.breakdown["wages"], line.breakdown["overtime"]), ("25.000", "7.500"))   # 5 days; 8 h x 0.625 x 1.5

    def test_a_friday_without_the_premium_is_an_ordinary_day(self):
        ravi = self.worker("Ravi", basis="daily", rate="5", ot=False, premium=False)
        self.attend(ravi, WORKING[:5] + [FRIDAY])
        line = self.line(self.labor_run(), ravi)
        self.assertEqual(line.breakdown["wages"], "30.000")             # six days at 5

    def test_paid_holidays_and_paid_leave_pay_the_standard_day_only_if_the_terms_say_so(self):
        Holiday.objects.create(date=date(2026, 3, 3), name="Test holiday")
        paid = self.worker("Paid", basis="daily", rate="5", ot=False, holidays=True)
        unpaid = self.worker("Unpaid", basis="daily", rate="5", ot=False, holidays=False)
        for emp in (paid, unpaid):
            self.attend(emp, [d for d in WORKING[:6] if d != date(2026, 3, 3)])
            self.attend(emp, [date(2026, 3, 12)], status="paid_leave")
        run = self.labor_run()
        self.assertEqual(self.line(run, paid).breakdown["wages"], "35.000")     # 5 worked + the holiday + the leave day
        self.assertEqual(self.line(run, unpaid).breakdown["wages"], "25.000")

    def test_an_hourly_worker_on_paid_holidays_gets_the_standard_hours_at_the_hourly_rate(self):
        Holiday.objects.create(date=date(2026, 3, 3), name="Test holiday")
        emp = self.worker("Hourly", basis="hourly", rate="1.25", ot=False, holidays=True)
        days = [d for d in WORKING[:5] if d != date(2026, 3, 3)]
        self.attend(emp, days)
        for d in days:
            self.hours(emp, d)
        self.assertEqual(self.line(self.labor_run(), emp).breakdown["wages"], "50.000")   # 4 x 8 x 1.25 + 8 x 1.25

    def test_pay_terms_that_change_part_way_are_each_used_on_their_own_days(self):
        ravi = self.worker("Ravi", basis="daily", rate="5", ot=False)
        labor_services.change_rate(ravi.labor_profile, effective_from=date(2026, 3, 16), wage_basis="daily", rate=D("6"),
                                   standard_hours=D("8"), overtime_eligible=False)
        self.attend(ravi, [date(2026, 3, 15), date(2026, 3, 16)])
        self.assertEqual(self.line(self.labor_run(), ravi).breakdown["wages"], "11.000")

    def test_payroll_agrees_with_the_labor_cost_report_for_daily_and_hourly_workers(self):
        Holiday.objects.create(date=date(2026, 3, 3), name="Test holiday")
        ravi = self.worker("Ravi", basis="daily", rate="5", holidays=True)
        hamid = self.worker("Hamid", basis="hourly", rate="1.25", premium=True)
        self.attend(ravi, [d for d in WORKING if d != date(2026, 3, 3)])
        self.attend(hamid, WORKING + [FRIDAY])
        self.full_month_hours(ravi, [d for d in WORKING if d != date(2026, 3, 3)], **{"2": "10"})
        self.full_month_hours(hamid, WORKING + [FRIDAY])
        self.claims()
        run = self.labor_run()
        facts, _ = reporting.collect_facts(self.hr, FIRST, LAST, engagement="direct", today=LAST)
        for emp in (ravi, hamid):
            mine = [f for f in facts.values() if f.employee == emp]
            line = self.line(run, emp)
            self.assertEqual(D(line.breakdown["wages"]), sum((f.wages for f in mine), D("0")).quantize(D("0.001")), emp.first_name)
            self.assertEqual(D(line.breakdown["overtime"]), sum((f.ot_approved for f in mine), D("0")), emp.first_name)
            self.assertEqual(self.codes(line, "block"), [], emp.first_name)


class NotSettledTests(LaborCase):
    """A line is blocked, never guessed, when what it rests on is not settled."""

    def clean_daily(self, name="Ravi", **kw):
        emp = self.worker(name, basis="daily", rate="5", **kw)
        self.attend(emp)
        self.full_month_hours(emp)
        return emp

    def test_a_clean_worker_has_no_problems(self):
        emp = self.clean_daily()
        self.claims()
        self.assertEqual(self.codes(self.line(self.labor_run(), emp)), [])

    def test_hours_that_are_still_draft_block_the_line(self):
        emp = self.worker("Ravi", basis="daily", rate="5")
        self.attend(emp, WORKING[:3])
        self.hours(emp, WORKING[0])
        self.hours(emp, WORKING[1], status="draft")
        self.hours(emp, WORKING[2])
        line = self.line(self.labor_run(), emp)
        self.assertIn("hours_draft", self.codes(line, "block"))
        self.assertIn("1 day of hours is still draft", [i["text"] for i in line.issues if i["code"] == "hours_draft"][0])

    def test_a_worked_day_with_no_hours_blocks_when_the_pay_depends_on_hours(self):
        emp = self.worker("Ravi", basis="daily", rate="5")                    # overtime yes: hours matter
        self.attend(emp, WORKING[:3])
        self.hours(emp, WORKING[0])
        self.hours(emp, WORKING[1])
        self.assertIn("hours_missing", self.codes(self.line(self.labor_run(), emp), "block"))

    def test_hours_do_not_matter_for_a_daily_worker_without_overtime_or_premium(self):
        emp = self.worker("Ravi", basis="daily", rate="5", ot=False, premium=False)
        self.attend(emp, WORKING[:3])
        self.assertNotIn("hours_missing", self.codes(self.line(self.labor_run(), emp)))

    def test_hours_on_a_day_attendance_does_not_show_as_worked_block(self):
        emp = self.worker("Ravi", basis="daily", rate="5", ot=False)
        self.attend(emp, WORKING[:2])
        self.attend(emp, [WORKING[2]], status="absent")
        self.hours(emp, WORKING[2])
        self.assertIn("hours_conflict", self.codes(self.line(self.labor_run(), emp), "block"))

    def test_confirmed_overtime_with_no_claim_blocks_until_it_is_prepared(self):
        emp = self.clean_daily()
        TimeEntry.objects.filter(employee=emp, date=MON2).update(hours=D("11"))
        run = self.labor_run()
        self.assertEqual(self.codes(self.line(run, emp), "block"), ["overtime_unprepared"])
        self.claims(approve=False)
        run = PayrollRun.objects.get(pk=run.pk)
        runs.calculate_run(run, self.hr)
        self.assertEqual(self.codes(self.line(run, emp), "block"), ["overtime_pending"])
        ot.decide(self.boss, OvertimeClaim.objects.all(), True)
        runs.calculate_run(run, self.hr)
        line = self.line(run, emp)
        self.assertEqual((self.codes(line, "block"), line.breakdown["overtime"]), ([], "2.344"))    # 3 h x 0.625 x 1.25

    def test_a_rejected_claim_is_simply_not_paid(self):
        emp = self.clean_daily()
        TimeEntry.objects.filter(employee=emp, date=MON2).update(hours=D("10"))
        ot.prepare_claims(self.hr, self.project, self.site, FIRST, LAST)
        ot.decide(self.boss, OvertimeClaim.objects.all(), False, "Not asked for")
        line = self.line(self.labor_run(), emp)
        self.assertEqual((self.codes(line, "block"), line.breakdown["overtime"]), ([], "0.000"))

    def test_working_days_with_no_attendance_block_for_a_daily_worker_but_only_warn_for_a_monthly_one(self):
        daily = self.worker("Ravi", basis="daily", rate="5", ot=False)
        monthly = self.worker("Saleh", basis="monthly", rate="300", ot=False)
        self.attend(daily, WORKING[:20])
        self.attend(monthly, WORKING[:20])
        run = self.labor_run()
        self.assertIn("attendance_missing", self.codes(self.line(run, daily), "block"))
        self.assertEqual(self.codes(self.line(run, monthly)), ["attendance_missing_monthly"])

    def test_pay_terms_must_exist_for_the_days_worked(self):
        emp = self.worker("Ravi", basis="daily", rate="5", ot=False, joined=date(2020, 1, 1))
        LaborRate.objects.filter(profile__employee=emp).update(effective_from=date(2026, 3, 10))
        self.attend(emp)
        self.assertIn("no_terms", self.codes(self.line(self.labor_run(), emp), "block"))

    def test_a_worker_with_nothing_this_month_gets_a_warning_not_a_block(self):
        emp = self.worker("Idle", basis="daily", rate="5", ot=False)
        self.attend(emp, [], status="present")
        Attendance.objects.bulk_create([Attendance(employee=emp, company=self.co, date=d, status="absent")
                                        for d in WORKING])
        line = self.line(self.labor_run(), emp)
        self.assertEqual((line.gross, self.codes(line)), (D("0.000"), ["no_pay"]))

    def test_the_payment_and_nationality_rules_are_the_same_as_for_staff(self):
        emp = self.worker("Ravi", basis="daily", rate="5", ot=False, pay=None, nationality="Bahrani")
        self.attend(emp, WORKING[:2])
        self.assertLessEqual({"nationality", "payment"}, set(self.codes(self.line(self.labor_run(), emp), "block")))

    def test_the_run_cannot_be_sent_while_any_line_is_blocked(self):
        self.worker("Ravi", basis="daily", rate="5", ot=False, pay=None)
        run = self.labor_run()
        with self.assertRaisesMessage(PayrollError, "a problem to fix first"):
            runs.submit_run(run, self.hr)


class SioTests(LaborCase):
    def month(self, emp):
        self.attend(emp)
        self.full_month_hours(emp)

    def test_a_bahraini_worker_pays_sio_on_the_wages_not_the_overtime(self):
        emp = self.worker("Ali", basis="daily", rate="5", nationality="Bahraini")
        self.month(emp)
        TimeEntry.objects.filter(employee=emp, date=MON2).update(hours=D("10"))
        self.claims()
        line = self.line(self.labor_run(), emp)
        self.assertEqual((line.sio_wage, line.sio_employee, line.sio_employer), (D("135.000"), D("6.750"), D("13.500")))
        self.assertEqual(line.net, D("136.563") - D("6.750"))

    def test_the_policy_wage_range_applies_and_other_nationalities_pay_none(self):
        high = self.worker("High", basis="monthly", rate="600", ot=False, nationality="Bahraini")
        self.attend(high)
        foreign = self.worker("Foreign", basis="monthly", rate="600", ot=False)
        self.attend(foreign)
        policy = self.policy
        policy.sio_wage_cap = D("400")
        policy.save()
        run = self.labor_run()
        self.assertEqual(self.line(run, high).sio_wage, D("400.000"))
        self.assertEqual((self.line(run, foreign).sio_employee, self.line(run, foreign).sio_employer), (D("0.000"), D("0.000")))

    def test_missing_rates_block_a_bahraini_but_not_anyone_else(self):
        from .payrun import PayrollPolicy
        PayrollPolicy.objects.filter(pk=self.policy.pk).update(sio_employee_rate=None)
        bah = self.worker("Ali", basis="monthly", rate="300", ot=False, nationality="Bahraini")
        ind = self.worker("Ravi", basis="monthly", rate="300", ot=False)
        self.attend(bah)
        self.attend(ind)
        run = self.labor_run()
        self.assertEqual(self.codes(self.line(run, bah), "block"), ["sio_rates"])
        self.assertEqual(self.codes(self.line(run, ind), "block"), [])


class WhoIsInTheRunTests(LaborCase):
    def test_only_direct_labor_who_have_joined_and_not_left_are_included(self):
        direct = self.worker("Ravi", basis="daily", rate="5", ot=False)
        contracted = self.worker("Contracted", basis="daily", rate="5", ot=False, engagement="contracted", pay=None)
        later = self.worker("Later", basis="daily", rate="5", ot=False, joined=date(2026, 4, 5))
        gone = self.worker("Gone", basis="daily", rate="5", ot=False)
        Employee.objects.filter(pk=gone.pk).update(status="separated")
        names = set(self.labor_run().lines.values_list("employee_name", flat=True))
        self.assertEqual(names, {"Ravi"})                                 # and none of the staff

    def test_a_labor_worker_with_no_profile_is_listed_so_it_is_not_forgotten(self):
        orphan = Employee.objects.create(company=self.co, employee_no="L-900", first_name="Orphan", worker_type="labor",
                                         joining_date=date(2020, 1, 1))
        run = self.labor_run()
        self.assertEqual([e.pk for e in labor_payroll.without_profile(run)], [orphan.pk])
        self.assertFalse(run.lines.filter(employee=orphan).exists())

    def test_staff_and_labor_runs_for_the_same_month_can_both_exist(self):
        self.new_run()
        run = self.labor_run()
        self.assertEqual(PayrollRun.objects.filter(status="draft").count(), 2)
        with self.assertRaisesMessage(PayrollError, "already has a direct labor payroll run for March 2026"):
            self.labor_run()
        runs.cancel_run(run)
        self.labor_run()

    def test_a_labor_run_needs_the_month_to_be_over(self):
        today = date.today()
        with self.assertRaisesMessage(PayrollError, "is not over yet"):
            runs.create_run(self.co, today.year, today.month, user=self.hr, kind="labor")
        with self.assertRaisesMessage(PayrollError, "Choose a staff run"):
            runs.create_run(self.co, 2026, 3, user=self.hr, kind="contractors")

    def test_recalculating_picks_up_changes_and_keeps_adjustments(self):
        emp = self.worker("Ravi", basis="daily", rate="5", ot=False)
        self.attend(emp, WORKING[:5])
        run = self.labor_run()
        runs.add_adjustment(run, emp, kind="deduction", category="advance", label="Advance", amount=D("10"))
        self.attend(emp, WORKING[5:10])
        runs.calculate_run(run, self.hr)
        line = self.line(run, emp)
        self.assertEqual((line.gross, line.deductions, line.net), (D("50.000"), D("10.000"), D("40.000")))
        self.assertEqual(run.adjustments.count(), 1)


class LockTests(LaborCase):
    """Once a labor run is approved, its month cannot quietly change: new pay terms, hours and overtime are refused."""

    def approved(self):
        self.ravi = self.worker("Ravi", basis="daily", rate="5")
        self.other = self.worker("Other", basis="daily", rate="5", ot=False)
        self.attend(self.ravi)
        self.attend(self.other)
        self.full_month_hours(self.ravi, **{"2": "10"})
        self.claims()
        run = self.labor_run()
        self.assertEqual(runs.blocking_count(run), 0)
        return self.approve(run)

    def test_new_pay_terms_cannot_start_inside_the_approved_month_but_can_start_after_it(self):
        self.approved()
        for start in (date(2026, 3, 15), date(2026, 3, 31)):
            with self.assertRaisesMessage(LaborError, "Payroll for March 2026 (IV1) has already been approved"):
                labor_services.change_rate(self.ravi.labor_profile, effective_from=start, wage_basis="daily",
                                           rate=D("6"), standard_hours=D("8"), overtime_eligible=True)
        labor_services.change_rate(self.ravi.labor_profile, effective_from=date(2026, 4, 1), wage_basis="daily",
                                   rate=D("6"), standard_hours=D("8"), overtime_eligible=True)
        self.assertEqual(self.ravi.labor_profile.rates.count(), 2)

    def test_overtime_for_the_month_cannot_be_prepared_decided_or_voided(self):
        self.approved()
        extra = self.hours(self.ravi, date(2026, 3, 13), "11")          # a late entry on a Friday, three hours over
        with self.assertRaisesMessage(LaborError, "preparing overtime claims is not allowed"):
            ot.prepare_claims(self.hr, self.project, self.site, FIRST, LAST)
        pending = OvertimeClaim.objects.create(
            entry=extra, employee=self.ravi, date=extra.date, project=self.project, location=self.site, day_kind="working",
            hours=D("3"), hourly_rate=D("0.625"), multiplier=D("1.25"), amount=D("2.344"),
            policy=ot.policy_on(self.co, extra.date))
        with self.assertRaisesMessage(LaborError, "deciding overtime is not allowed"):
            ot.decide(self.boss, OvertimeClaim.objects.filter(pk=pending.pk), True)
        pending.refresh_from_db()
        self.assertEqual(pending.status, "pending")
        with self.assertRaisesMessage(LaborError, "voiding overtime claims is not allowed"):
            ot.void_claims(self.hr, self.project, self.site, FIRST, LAST)
        self.assertEqual(OvertimeClaim.objects.count(), 2)

    def test_hours_cannot_be_reopened_split_or_saved_for_the_month(self):
        from apps.labor import hours_split, timekeeping
        self.approved()
        with self.assertRaisesMessage(LaborError, "reopening hours is not allowed"):
            timekeeping.reopen_period(self.hr, self.project, self.site, FIRST, LAST)
        with self.assertRaisesMessage(LaborError, "changing hours is not allowed"):
            timekeeping.save_hours(self.hr, self.project, self.site, FIRST, LAST, {})
        with self.assertRaisesMessage(LaborError, "dividing hours is not allowed"):
            hours_split.split_days(self.hr, self.ravi, [{"project": self.project, "regular": D("8"), "overtime": D("0")}],
                                   FIRST, LAST)
        self.assertEqual(TimeEntry.objects.filter(status="confirmed", employee=self.ravi).count(), 27)

    def test_the_month_after_is_free_and_so_is_a_worker_who_was_not_in_the_run(self):
        from apps.labor import timekeeping
        self.approved()
        self.assertEqual(timekeeping.save_hours(self.hr, self.project, self.site, date(2026, 4, 1), date(2026, 4, 30), {})["created"], 0)
        newcomer = self.worker("Newcomer", basis="daily", rate="5", joined=date(2026, 3, 20))
        labor_services.change_rate(newcomer.labor_profile, effective_from=date(2026, 3, 25), wage_basis="daily",
                                   rate=D("6"), standard_hours=D("8"), overtime_eligible=True)

    def test_a_draft_run_a_cancelled_run_and_a_staff_run_lock_nothing(self):
        ravi = self.worker("Ravi", basis="daily", rate="5", ot=False)
        self.attend(ravi, WORKING[:3])
        labor_run = self.labor_run()
        labor_services.change_rate(ravi.labor_profile, effective_from=date(2026, 3, 15), wage_basis="daily", rate=D("6"),
                                   standard_hours=D("8"), overtime_eligible=False)          # a draft locks nothing
        runs.cancel_run(labor_run)
        self.approve(self.new_run())                                                         # an approved STAFF run
        labor_services.change_rate(ravi.labor_profile, effective_from=date(2026, 3, 20), wage_basis="daily", rate=D("7"),
                                   standard_hours=D("8"), overtime_eligible=False)

    def test_a_paid_run_stays_closed(self):
        run = self.approved()
        runs.mark_paid(run, list(run.lines.values_list("pk", flat=True)), date(2026, 4, 2))
        run.refresh_from_db()
        self.assertEqual(run.status, "paid")
        with self.assertRaises(LaborError):
            labor_services.change_rate(self.ravi.labor_profile, effective_from=date(2026, 3, 20), wage_basis="daily",
                                       rate=D("6"), standard_hours=D("8"), overtime_eligible=True)

    def test_the_lock_registry_itself(self):
        from apps.labor import locks
        locks.ensure_open([], FIRST, LAST, "anything")                                      # nobody: nothing to check
        locks.ensure_open([self.hr_emp.pk], FIRST, LAST, "anything")                        # no run: open
        self.assertIsNone(__import__("apps.payroll.locks", fromlist=["x"]).labor_period_closed([self.hr_emp.pk], FIRST, None))


class RunFlowTests(LaborCase):
    def test_a_labor_run_goes_through_the_same_approval_and_payment_flow(self):
        ravi = self.worker("Ravi", basis="daily", rate="5", ot=False, pay="cash")
        saleh = self.worker("Saleh", basis="monthly", rate="300", ot=False, pay="bank")
        self.attend(ravi)
        self.attend(saleh)
        run = self.approve(self.labor_run())
        self.assertEqual(run.status, "approved")
        t = runs.totals(run)
        self.assertEqual((t["people"], t["gross"], t["cash"], t["bank"]), (2, D("435.000"), D("135.000"), D("300.000")))
        self.assertEqual(runs.mark_paid(run, [l.pk for l in runs.unpaid_lines(run, "cash")], date(2026, 4, 2)), 1)
        self.assertEqual(runs.mark_paid(run, [l.pk for l in runs.unpaid_lines(run, "bank")], date(2026, 4, 3)), 1)
        run.refresh_from_db()
        self.assertEqual(run.status, "paid")

    def test_the_audit_log_hides_the_labor_figures_too(self):
        from apps.audit.models import AuditEvent
        ravi = self.worker("Ravi", basis="daily", rate="5", ot=False)
        self.attend(ravi, WORKING[:10])
        self.labor_run()
        text = str([e.changes for e in AuditEvent.objects.filter(module="payroll", content_type__model="payrollline")])
        for secret in ("50.000", VALID_IBAN):
            self.assertNotIn(secret, text)
