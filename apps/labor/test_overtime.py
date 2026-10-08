from datetime import date
from decimal import Decimal

from django.db import IntegrityError, transaction

from apps.attendance.models import Attendance
from apps.audit.models import AuditEvent
from apps.employees.models import Employee
from apps.scheduling.models import Holiday

from . import overtime_services as ot
from . import services, sitesheet, timekeeping
from .overtime import OvertimeClaim, OvertimePolicy
from .test_sitesheet import FRI, SAT, SUN
from .test_timekeeping import MON, TUE, TimeCase
from .timesheet import TimeEntry

D = Decimal
Claim = OvertimeClaim


class OvertimeCase(TimeCase):
    def setUp(self):
        super().setUp()
        self.policy = self.set_rules(date(2026, 1, 1))

    def set_rules(self, start, company=None, **over):
        terms = dict(working_day_multiplier=D("1.25"), weekly_off_multiplier=D("1.50"), holiday_multiplier=D("1.50"),
                     all_hours_on_days_off=True, monthly_divisor=30)
        terms.update(over)
        return ot.set_policy(company or self.co, effective_from=start, **terms)

    def confirmed(self, posted=None, first=SUN, last=SAT):
        """Standard hours for every worked day, plus whatever was typed, then confirmed."""
        self.hours(posted or {}, fill="standard", first=first, last=last)
        timekeeping.confirm_period(self.root, self.p1, self.site1, first, last)

    def prepare(self, first=SUN, last=SAT):
        return ot.prepare_claims(self.root, self.p1, self.site1, first, last)


class PolicyTests(OvertimeCase):
    def test_a_company_has_no_rules_until_someone_sets_them(self):
        self.assertIsNone(ot.policy_on(self.other, date(2026, 3, 1)))
        OvertimePolicy.objects.all().delete()
        self.confirmed({self.hkey(self.emp, MON): "10"})
        with self.assertRaisesMessage(services.LaborError, "has no overtime decision"):
            self.prepare()

    def test_new_rules_end_the_old_ones_the_day_before_and_are_effective_dated(self):
        new = self.set_rules(date(2026, 3, 5), working_day_multiplier=D("1.5"))
        self.policy.refresh_from_db()
        self.assertEqual(self.policy.effective_to, date(2026, 3, 4))
        self.assertEqual(ot.policy_on(self.co, date(2026, 3, 4)), self.policy)
        self.assertEqual(ot.policy_on(self.co, date(2026, 3, 5)), new)
        self.assertIsNone(ot.policy_on(self.co, date(2025, 12, 31)))

    def test_new_rules_must_start_after_the_current_ones(self):
        for day in (date(2026, 1, 1), date(2025, 6, 1)):
            with self.assertRaisesMessage(services.LaborError, "must start after"):
                self.set_rules(day)
        self.assertEqual(OvertimePolicy.objects.filter(company=self.co).count(), 1)

    def test_multipliers_and_the_month_length_are_checked(self):
        for bad in ({"working_day_multiplier": D("0.9")}, {"holiday_multiplier": D("5.5")}):
            with self.assertRaises(services.LaborError):
                self.set_rules(date(2026, 6, 1), **bad)
        for bad in (19, 32):
            with self.assertRaises(services.LaborError):
                self.set_rules(date(2026, 6, 1), monthly_divisor=bad)

    def test_only_one_open_policy_per_company(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            OvertimePolicy.objects.create(company=self.co, effective_from=date(2026, 8, 1))


class CalculationTests(OvertimeCase):
    def entry(self, hours, expected="8", eligible=True):
        return TimeEntry(hours=D(hours), expected_hours=D(expected), overtime_eligible=eligible)

    def test_hours_above_the_expected_day_on_a_normal_day(self):
        w = Claim.DayKind.WORKING
        self.assertEqual(ot.overtime_hours(self.entry("10"), w, self.policy), D("2"))
        self.assertEqual(ot.overtime_hours(self.entry("8"), w, self.policy), D("0"))
        self.assertEqual(ot.overtime_hours(self.entry("6"), w, self.policy), D("0"))
        self.assertEqual(ot.overtime_hours(self.entry("6", expected="4"), w, self.policy), D("2"))     # a half day

    def test_every_hour_on_a_day_off_when_the_rules_say_so(self):
        off = Claim.DayKind.WEEKLY_OFF
        self.assertEqual(ot.overtime_hours(self.entry("8"), off, self.policy), D("8"))
        beyond = self.set_rules(date(2026, 6, 1), all_hours_on_days_off=False)
        self.assertEqual(ot.overtime_hours(self.entry("8"), off, beyond), D("0"))
        self.assertEqual(ot.overtime_hours(self.entry("10"), off, beyond), D("2"))

    def test_workers_not_eligible_get_nothing(self):
        for kind in Claim.DayKind.values:
            self.assertEqual(ot.overtime_hours(self.entry("12", eligible=False), kind, self.policy), D("0"))

    def test_hourly_rate_from_a_daily_wage_and_from_a_monthly_salary(self):
        daily = self.worker1.current_rate
        self.assertEqual(ot.hourly_rate(daily, self.policy), D("0.6250"))                  # 5 / 8
        monthly = self.make_other("Salaried")
        services.change_rate(monthly.labor_profile, effective_from=date(2026, 2, 1), wage_basis="monthly", rate=D("300"),
                             standard_hours=D("8"), overtime_eligible=True)
        self.assertEqual(ot.hourly_rate(monthly.labor_profile.current_rate, self.policy), D("1.2500"))   # 300 / 30 / 8
        self.assertEqual(ot.hourly_rate(monthly.labor_profile.current_rate,
                                        self.set_rules(date(2026, 6, 1), monthly_divisor=26)), D("1.4423"))


class PrepareTests(OvertimeCase):
    def test_a_normal_day_claim_has_the_whole_working_copied_onto_it(self):
        self.confirmed({self.hkey(self.emp, MON): "10"})
        created, skipped = self.prepare()
        self.assertEqual((created, skipped), (1, []))
        c = Claim.objects.get()
        self.assertEqual((c.employee, c.date, c.day_kind, c.hours, c.status), (self.emp, MON, "working", D("2"), "pending"))
        self.assertEqual((c.hourly_rate, c.multiplier, c.policy), (D("0.6250"), D("1.25"), self.policy))
        self.assertEqual(c.amount, D("1.563"))                                   # 2 x 0.625 x 1.25 = 1.5625, rounded up
        self.assertEqual((c.project, c.location, c.company_id), (self.p1, self.site1, self.co.pk))
        self.assertEqual(c.entry, TimeEntry.objects.get(employee=self.emp, date=MON))

    def test_only_confirmed_hours_produce_claims_and_only_once(self):
        self.hours({self.hkey(self.emp, MON): "10"}, fill="standard")
        with self.assertRaisesMessage(services.LaborError, "no confirmed hours"):
            self.prepare()                                                       # still a draft
        timekeeping.confirm_period(self.root, self.p1, self.site1, SUN, SAT)
        self.assertEqual(self.prepare()[0], 1)
        self.assertEqual(self.prepare(), (0, []))                                 # preparing twice adds nothing
        self.assertEqual(Claim.objects.count(), 1)

    def test_days_with_no_overtime_make_no_claim(self):
        self.confirmed()                                                         # everyone on exactly 8 hours
        self.assertEqual(self.prepare(), (0, []))
        self.assertEqual(Claim.objects.count(), 0)

    def test_working_a_weekly_off_pays_every_hour_at_the_weekly_off_multiplier(self):
        sitesheet.save_sheet(self.root, self.p1, self.site1, FRI, FRI, {self.key(self.emp, FRI): "present"}, "")
        self.confirmed()
        created, _ = self.prepare()
        c = Claim.objects.get(date=FRI)
        self.assertEqual((c.day_kind, c.hours, c.multiplier, c.amount), ("weekly_off", D("8"), D("1.50"), D("7.500")))

    def test_working_a_public_holiday(self):
        Holiday.objects.create(date=TUE, name="Test holiday")
        self.confirmed()
        self.prepare()
        c = Claim.objects.get(date=TUE)
        self.assertEqual((c.day_kind, c.hours, c.multiplier), ("holiday", D("8"), D("1.50")))

    def test_a_claim_keeps_the_rules_it_was_made_under(self):
        self.confirmed({self.hkey(self.emp, MON): "10"})
        self.prepare()
        self.set_rules(date(2026, 3, 10), working_day_multiplier=D("2"))
        services.change_rate(self.worker1, effective_from=date(2026, 3, 10), wage_basis="daily", rate=D("9"),
                             standard_hours=D("8"), overtime_eligible=True)
        c = Claim.objects.get()
        self.assertEqual((c.multiplier, c.hourly_rate, c.amount, c.policy), (D("1.25"), D("0.6250"), D("1.563"), self.policy))

    def test_the_rules_in_force_on_each_day_are_used(self):
        self.set_rules(date(2026, 3, 3), working_day_multiplier=D("2"))
        self.confirmed({self.hkey(self.emp, MON): "10", self.hkey(self.emp, date(2026, 3, 4)): "10"})
        self.prepare()
        by = {c.date: c.multiplier for c in Claim.objects.all()}
        self.assertEqual(by, {MON: D("1.25"), date(2026, 3, 4): D("2")})

    def test_one_claim_per_entry(self):
        self.confirmed({self.hkey(self.emp, MON): "10"})
        self.prepare()
        with self.assertRaises(IntegrityError), transaction.atomic():
            Claim.objects.create(entry=Claim.objects.get().entry, employee=self.emp, date=MON, project=self.p1,
                                 location=self.site1, day_kind="working", hours=D("1"), hourly_rate=D("1"),
                                 multiplier=D("1.25"), amount=D("1"), policy=self.policy)


class DecisionTests(OvertimeCase):
    def setUp(self):
        super().setUp()
        self.confirmed({self.hkey(self.emp, MON): "10", self.hkey(self.emp, TUE): "11"})
        self.prepare()

    def test_approving_records_who_and_when(self):
        decided, skipped = ot.decide(self.root, Claim.objects.all(), True)
        self.assertEqual((decided, skipped), (2, 0))
        c = Claim.objects.get(date=MON)
        self.assertEqual((c.status, c.decided_by), ("approved", self.root))
        self.assertIsNotNone(c.decided_at)

    def test_rejecting_needs_a_reason_and_keeps_it(self):
        with self.assertRaisesMessage(services.LaborError, "Give a reason"):
            ot.decide(self.root, Claim.objects.all(), False, "  ")
        self.assertEqual(Claim.objects.filter(status="pending").count(), 2)
        ot.decide(self.root, Claim.objects.filter(date=TUE), False, "Not authorised")
        c = Claim.objects.get(date=TUE)
        self.assertEqual((c.status, c.decision_note), ("rejected", "Not authorised"))

    def test_a_decision_is_final(self):
        ot.decide(self.root, Claim.objects.filter(date=MON), True)
        decided, skipped = ot.decide(self.root, Claim.objects.all(), False, "changed my mind")
        self.assertEqual((decided, skipped), (1, 1))
        self.assertEqual(Claim.objects.get(date=MON).status, "approved")

    def test_summary_totals_by_status(self):
        ot.decide(self.root, Claim.objects.filter(date=MON), True)
        s = ot.summary(Claim.objects.all())
        self.assertEqual((s["approved"]["count"], s["approved"]["hours"], s["pending"]["hours"]), (1, D("2"), D("3")))

    def test_the_log_shows_the_claim_and_the_decision_against_the_worker(self):
        ot.decide(self.root, Claim.objects.filter(date=MON), True)
        events = AuditEvent.objects.filter(module="labor", subject_employee_id=self.emp.pk)
        self.assertGreaterEqual(events.filter(object_id=str(Claim.objects.get(date=MON).pk), action="create").count(), 1)
        self.assertTrue(events.filter(object_id=str(Claim.objects.get(date=MON).pk), action="update").exists())


class ReopenGuardTests(OvertimeCase):
    def setUp(self):
        super().setUp()
        self.confirmed({self.hkey(self.emp, MON): "10"})
        self.prepare()

    def reopen(self):
        return timekeeping.reopen_period(self.root, self.p1, self.site1, SUN, SAT)

    def test_reopening_removes_pending_claims_so_they_are_worked_out_again(self):
        self.assertEqual(self.reopen(), 6)
        self.assertEqual(Claim.objects.count(), 0)
        self.hours({self.hkey(self.emp, MON): "11"})
        timekeeping.confirm_period(self.root, self.p1, self.site1, SUN, SAT)
        self.prepare()
        self.assertEqual(Claim.objects.get().hours, D("3"))

    def test_decided_claims_block_reopening_until_they_are_voided(self):
        ot.decide(self.root, Claim.objects.all(), True)
        with self.assertRaisesMessage(services.LaborError, "already been decided"):
            self.reopen()
        self.assertEqual(TimeEntry.objects.filter(status="confirmed").count(), 6)
        self.assertEqual(ot.void_claims(self.root, self.p1, self.site1, SUN, SAT), 1)
        self.assertEqual(self.reopen(), 6)

    def test_rejected_claims_also_block_so_the_decision_is_not_lost_silently(self):
        ot.decide(self.root, Claim.objects.all(), False, "no")
        with self.assertRaisesMessage(services.LaborError, "already been decided"):
            self.reopen()

    def test_voiding_with_nothing_to_void(self):
        Claim.objects.all().delete()
        with self.assertRaisesMessage(services.LaborError, "no overtime claims"):
            ot.void_claims(self.root, self.p1, self.site1, SUN, SAT)

    def test_other_companies_cannot_prepare_or_void_for_a_site_they_cannot_see(self):
        from django.contrib.auth import get_user_model
        outsider = get_user_model().objects.create_user("out")
        with self.assertRaises(services.LaborError):
            ot.prepare_claims(outsider, self.p1, self.site1, SUN, SAT)
        with self.assertRaises(services.LaborError):
            ot.void_claims(outsider, self.p1, self.site1, SUN, SAT)
        self.assertEqual(Claim.objects.count(), 1)


class NoOvertimeCompanyTests(OvertimeCase):
    """One company pays overtime, the other has decided it does not."""

    def setUp(self):
        super().setUp()
        from apps.organization.models import Project
        self.pb = Project.objects.create(company=self.other, code="X1", name="B Tower", location=self.site2)
        self.b = services.create_labor_worker(
            Employee(company=self.other, first_name="Salim", joining_date=date(2026, 1, 1)), engagement="direct",
            trade=self.mason, wage_basis="daily", rate=D("5")).employee
        from . import deployment
        deployment.allocate(self.b, project=self.pb, location=self.site2, effective_from=date(2026, 1, 5))
        sitesheet.save_sheet(self.root, self.pb, self.site2, SUN, SAT, {}, "present")

    def b_hours(self, posted=None, fill="standard"):
        return timekeeping.save_hours(self.root, self.pb, self.site2, SUN, SAT, posted or {}, fill)

    def b_sheet(self):
        return timekeeping.build_timesheet(self.root, self.pb, self.site2, SUN, SAT)

    def no_overtime(self, start=date(2026, 1, 1)):
        return ot.set_policy(self.other, effective_from=start, overtime_applies=False, working_day_multiplier=D("1.25"),
                             weekly_off_multiplier=D("1.5"), holiday_multiplier=D("1.5"), all_hours_on_days_off=True,
                             monthly_divisor=30)

    def test_the_mode_is_undecided_then_none_then_applies(self):
        self.assertEqual(ot.overtime_mode(self.other, date(2026, 3, 1))[0], "undecided")
        self.no_overtime()
        self.assertEqual(ot.overtime_mode(self.other, date(2026, 3, 1))[0], "none")
        self.assertEqual(ot.overtime_mode(self.co, date(2026, 3, 1))[0], "applies")
        self.set_rules(date(2026, 7, 1), company=self.other)
        self.assertEqual(ot.overtime_mode(self.other, date(2026, 8, 1))[0], "applies")
        self.assertEqual(ot.overtime_mode(self.other, date(2026, 3, 1))[0], "none")       # history keeps its answer

    def test_hours_are_recorded_but_none_count_as_overtime(self):
        self.no_overtime()
        self.b_hours({f"h_{self.b.pk}_20260302": "11"})
        entry = TimeEntry.objects.get(employee=self.b, date=MON)
        self.assertEqual((entry.hours, entry.overtime_eligible, entry.overtime_hours), (D("11"), False, D("0")))
        sheet = self.b_sheet()
        self.assertEqual((sheet.overtime_mode, sheet.overtime), ("none", D("0")))
        self.assertEqual(sheet.hours, D("11") + 5 * 8)

    def test_the_other_company_still_gets_overtime(self):
        self.no_overtime()
        self.hours({self.hkey(self.emp, MON): "10"}, fill="standard")
        self.assertEqual(TimeEntry.objects.get(employee=self.emp, date=MON).overtime_hours, D("2"))
        self.assertEqual(self.tsheet().overtime_mode, "applies")

    def test_an_undecided_company_falls_back_to_the_workers_own_setting(self):
        self.b_hours({f"h_{self.b.pk}_20260302": "10"})
        self.assertEqual(TimeEntry.objects.get(employee=self.b, date=MON).overtime_hours, D("2"))
        self.assertEqual(self.b_sheet().overtime_mode, "undecided")

    def test_no_claims_are_ever_made_for_a_no_overtime_company(self):
        self.no_overtime()
        self.b_hours({f"h_{self.b.pk}_20260302": "11"})
        timekeeping.confirm_period(self.root, self.pb, self.site2, SUN, SAT)
        self.assertEqual(ot.prepare_claims(self.root, self.pb, self.site2, SUN, SAT), (0, []))
        self.assertEqual(Claim.objects.filter(employee=self.b).count(), 0)

    def test_switching_overtime_off_fixes_draft_hours_from_that_date_only(self):
        self.b_hours({f"h_{self.b.pk}_20260302": "10", f"h_{self.b.pk}_20260304": "10"})        # drafts, undecided: eligible
        self.no_overtime(start=date(2026, 3, 3))
        by = {e.date: e.overtime_eligible for e in TimeEntry.objects.filter(employee=self.b)}
        self.assertTrue(by[MON])                                          # before the start date: as it was
        self.assertFalse(by[date(2026, 3, 4)])                            # from the start date: no overtime
        self.assertFalse(by[date(2026, 3, 5)])

    def test_confirmed_hours_are_history_and_are_not_touched(self):
        self.b_hours({f"h_{self.b.pk}_20260304": "10"})
        timekeeping.confirm_period(self.root, self.pb, self.site2, SUN, SAT)
        self.no_overtime(start=date(2026, 3, 3))
        self.assertTrue(TimeEntry.objects.get(employee=self.b, date=date(2026, 3, 4)).overtime_eligible)

    def test_switching_back_on_restores_the_workers_own_setting_and_respects_it(self):
        self.no_overtime()
        self.b_hours({f"h_{self.b.pk}_20260304": "10"})
        self.assertFalse(TimeEntry.objects.get(employee=self.b, date=date(2026, 3, 4)).overtime_eligible)
        self.set_rules(date(2026, 3, 3), company=self.other)
        self.assertTrue(TimeEntry.objects.get(employee=self.b, date=date(2026, 3, 4)).overtime_eligible)
        services.change_rate(self.b.labor_profile, effective_from=date(2026, 3, 5), wage_basis="daily", rate=D("5"),
                             standard_hours=D("8"), overtime_eligible=False)
        self.assertFalse(self.b_sheet().rows[0].cells[4].eligible)        # Thursday 5 March: marked not eligible stays so

    def test_workers_keep_their_own_flag_so_nothing_needs_redoing_if_overtime_starts_later(self):
        self.no_overtime()
        self.assertTrue(self.b.labor_profile.current_rate.overtime_eligible)

    def test_confirmed_hours_that_fall_under_a_later_no_overtime_decision_make_no_claim(self):
        self.b_hours({f"h_{self.b.pk}_20260304": "10"})                    # undecided at the time: eligible
        timekeeping.confirm_period(self.root, self.pb, self.site2, SUN, SAT)
        self.no_overtime(start=date(2026, 3, 3))                           # confirmed hours are history and stay as they were
        self.assertTrue(TimeEntry.objects.get(employee=self.b, date=date(2026, 3, 4)).overtime_eligible)
        self.assertEqual(ot.prepare_claims(self.root, self.pb, self.site2, SUN, SAT), (0, []))
        self.assertEqual(Claim.objects.filter(employee=self.b).count(), 0)         # but the company's decision wins
