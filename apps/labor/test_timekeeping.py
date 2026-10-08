from datetime import date, timedelta
from decimal import Decimal

from django.db import IntegrityError, transaction

from apps.attendance.models import Attendance
from apps.audit.models import AuditEvent
from apps.employees.models import Employee

from . import deployment, services, sitesheet, timekeeping
from .allocation import WorkOrder
from .test_sitesheet import FRI, SAT, SUN, SheetCase
from .timesheet import TimeEntry

D = Decimal
MON, TUE = date(2026, 3, 2), date(2026, 3, 3)


class TimeCase(SheetCase):
    def setUp(self):
        super().setUp()
        sitesheet.save_sheet(self.root, self.p1, self.site1, SUN, SAT, {}, "present")      # Sun-Thu and Sat worked

    def tsheet(self, first=SUN, last=SAT, project=None, location=None):
        return timekeeping.build_timesheet(self.root, project or self.p1, location or self.site1, first, last)

    def hours(self, posted=None, fill="", first=SUN, last=SAT, project=None, location=None):
        return timekeeping.save_hours(self.root, project or self.p1, location or self.site1, first, last,
                                      posted or {}, fill)

    def hkey(self, employee, d):
        return f"h_{employee.pk}_{d:%Y%m%d}"

    def cell(self, d, employee=None):
        row = next(r for r in self.tsheet().rows if r.employee == (employee or self.emp))
        return next(c for c in row.cells if c.date == d)


class BuildTests(TimeCase):
    def test_hours_can_be_entered_only_for_worked_days_and_expect_the_standard_hours(self):
        c = self.cell(MON)
        self.assertTrue(c.can_enter)
        self.assertEqual((c.expected, c.eligible), (D("8.00"), True))
        self.assertFalse(self.cell(FRI).can_enter)                          # the weekly off: no attendance, nothing worked
        self.assertIsNone(self.cell(FRI).expected)

    def test_half_days_expect_half_the_hours_and_absent_days_take_none(self):
        deployment.allocate(self.b_worker(), project=self.p1, location=self.site1, effective_from=date(2026, 1, 5))
        other = Employee.objects.get(first_name="Imran")
        sitesheet.save_sheet(self.root, self.p1, self.site1, SUN, SAT,
                             {self.key(other, SUN): "half_day", self.key(other, MON): "absent"}, "")
        row = next(r for r in self.tsheet().rows if r.employee == other)
        by = {c.date: c for c in row.cells}
        self.assertEqual(by[SUN].expected, D("4.00"))
        self.assertTrue(by[SUN].can_enter)
        self.assertFalse(by[MON].can_enter)

    def b_worker(self):
        return self.make_other("Imran")

    def test_a_rest_day_that_was_worked_can_be_entered(self):
        sitesheet.save_sheet(self.root, self.p1, self.site1, FRI, FRI, {self.key(self.emp, FRI): "present"}, "")
        self.assertTrue(self.cell(FRI).can_enter)

    def test_days_after_the_worker_moved_away_are_not_offered(self):
        deployment.allocate(self.emp, project=self.p2, location=self.site2, effective_from=date(2026, 3, 4))
        self.assertTrue(self.cell(TUE).can_enter)
        self.assertFalse(self.cell(date(2026, 3, 5)).covered)
        self.assertFalse(self.cell(date(2026, 3, 5)).can_enter)


class SaveHoursTests(TimeCase):
    def test_saves_typed_hours_charged_to_the_allocation(self):
        wo = WorkOrder.objects.create(project=self.p1, code="WO-1", name="Foundations")
        deployment.release(self.emp, last_day=date(2026, 2, 28))
        deployment.allocate(self.emp, project=self.p1, location=self.site1, work_order=wo, effective_from=date(2026, 3, 1))
        r = self.hours({self.hkey(self.emp, MON): "10", self.hkey(self.emp, TUE): "7.5"})
        self.assertEqual((r["created"], r["updated"], r["cleared"], r["skipped"]), (2, 0, 0, []))
        e = TimeEntry.objects.get(employee=self.emp, date=MON)
        self.assertEqual((e.hours, e.expected_hours, e.project, e.location, e.work_order, e.status),
                         (D("10"), D("8"), self.p1, self.site1, wo, "draft"))
        self.assertEqual(e.company_id, self.co.pk)

    def test_overtime_is_the_hours_above_what_the_day_expected(self):
        self.hours({self.hkey(self.emp, MON): "10", self.hkey(self.emp, TUE): "6"})
        long, short = (TimeEntry.objects.get(employee=self.emp, date=d) for d in (MON, TUE))
        self.assertEqual((long.regular_hours, long.overtime_hours), (D("8"), D("2")))
        self.assertEqual((short.regular_hours, short.overtime_hours), (D("6"), D("0")))
        row = next(r for r in self.tsheet().rows if r.employee == self.emp)
        self.assertEqual((row.hours, row.overtime), (D("16"), D("2")))

    def test_workers_not_eligible_for_overtime_have_none(self):
        services.change_rate(self.worker1, effective_from=date(2026, 2, 1), wage_basis="daily", rate=D("5"),
                             standard_hours=D("8"), overtime_eligible=False)
        self.hours({self.hkey(self.emp, MON): "11"})
        e = TimeEntry.objects.get(employee=self.emp, date=MON)
        self.assertEqual((e.overtime_eligible, e.overtime_hours), (False, D("0")))

    def test_fill_standard_covers_empty_worked_days_only(self):
        r = self.hours({self.hkey(self.emp, MON): "9"}, fill="standard")
        days = dict(TimeEntry.objects.filter(employee=self.emp).values_list("date", "hours"))
        self.assertEqual(set(days), {date(2026, 3, d) for d in (1, 2, 3, 4, 5, 7)})          # the six worked days
        self.assertEqual((days[MON], days[SUN]), (D("9"), D("8")))                          # typed wins, others standard
        self.assertEqual(r["created"], 6)

    def test_editing_and_clearing_a_draft_and_leaving_it_alone(self):
        self.hours({self.hkey(self.emp, MON): "9", self.hkey(self.emp, TUE): "8"})
        r = self.hours({self.hkey(self.emp, MON): "10.5", self.hkey(self.emp, TUE): ""})
        self.assertEqual((r["created"], r["updated"], r["cleared"]), (0, 1, 1))
        self.assertEqual(TimeEntry.objects.get(employee=self.emp, date=MON).hours, D("10.5"))
        self.assertFalse(TimeEntry.objects.filter(employee=self.emp, date=TUE).exists())
        again = self.hours({self.hkey(self.emp, MON): "10.5"})
        self.assertEqual((again["created"], again["updated"], again["cleared"]), (0, 0, 0))
        untouched = self.hours({})                                           # a missing box never clears anything
        self.assertEqual(untouched["cleared"], 0)
        self.assertTrue(TimeEntry.objects.filter(employee=self.emp, date=MON).exists())

    def test_bad_values_are_reported_and_the_rest_is_saved(self):
        r = self.hours({self.hkey(self.emp, SUN): "abc", self.hkey(self.emp, MON): "0", self.hkey(self.emp, TUE): "25",
                        self.hkey(self.emp, date(2026, 3, 4)): "7.555", self.hkey(self.emp, date(2026, 3, 5)): "8"})
        self.assertEqual(r["created"], 1)
        self.assertEqual(len(r["skipped"]), 4)
        self.assertTrue(any("not a number" in s for s in r["skipped"]))
        self.assertTrue(any("two decimals" in s for s in r["skipped"]))
        self.assertTrue(any("between 0.25 and 24" in s for s in r["skipped"]))
        self.assertEqual(TimeEntry.objects.count(), 1)

    def test_only_cells_the_server_offers_are_read(self):
        other = self.make_other("Imran")
        deployment.allocate(other, project=self.p2, location=self.site2, effective_from=date(2026, 1, 5))
        self.hours({self.hkey(other, MON): "8",                         # another site's worker
                    self.hkey(self.emp, FRI): "8",                      # a day that was not worked
                    "h_999999_20260302": "8"})
        self.assertEqual(TimeEntry.objects.count(), 0)

    def test_a_pay_change_never_alters_an_old_entry(self):
        self.hours({self.hkey(self.emp, MON): "10"})
        services.change_rate(self.worker1, effective_from=date(2026, 3, 3), wage_basis="daily", rate=D("6"),
                             standard_hours=D("9"), overtime_eligible=True)
        e = TimeEntry.objects.get(employee=self.emp, date=MON)
        self.assertEqual((e.expected_hours, e.overtime_hours), (D("8"), D("2")))
        self.assertEqual(self.cell(TUE).expected, D("9.00"))             # but the new terms apply from then on

    def test_fill_must_be_known(self):
        with self.assertRaises(services.LaborError):
            self.hours(fill="all")

    def test_hours_are_audited_against_the_worker(self):
        before = AuditEvent.objects.filter(module="labor", subject_employee_id=self.emp.pk, action="create").count()
        self.hours(fill="standard")
        after = AuditEvent.objects.filter(module="labor", subject_employee_id=self.emp.pk, action="create").count()
        self.assertEqual(after - before, 6)


class ConfirmTests(TimeCase):
    def test_cannot_confirm_while_worked_days_have_no_hours(self):
        self.hours({self.hkey(self.emp, MON): "8"})
        with self.assertRaisesMessage(services.LaborError, "5 worked days still have no hours"):
            timekeeping.confirm_period(self.root, self.p1, self.site1, SUN, SAT)
        self.assertEqual(TimeEntry.objects.filter(status="confirmed").count(), 0)

    def test_nothing_to_confirm(self):
        Attendance.objects.all().delete()
        with self.assertRaisesMessage(services.LaborError, "no draft hours"):
            timekeeping.confirm_period(self.root, self.p1, self.site1, SUN, SAT)

    def test_confirming_locks_the_hours_and_logs_each_change(self):
        self.hours(fill="standard")
        before = AuditEvent.objects.filter(module="labor", subject_employee_id=self.emp.pk, action="update").count()
        self.assertEqual(timekeeping.confirm_period(self.root, self.p1, self.site1, SUN, SAT), 6)
        self.assertEqual(TimeEntry.objects.filter(status="confirmed").count(), 6)
        after = AuditEvent.objects.filter(module="labor", subject_employee_id=self.emp.pk, action="update").count()
        self.assertEqual(after - before, 6)
        c = self.cell(MON)
        self.assertTrue(c.locked and not c.can_enter)
        r = self.hours({self.hkey(self.emp, MON): "12"}, fill="standard")        # locked: nothing changes
        self.assertEqual((r["created"], r["updated"], r["cleared"]), (0, 0, 0))
        self.assertEqual(TimeEntry.objects.get(employee=self.emp, date=MON).hours, D("8"))

    def test_reopening_makes_them_editable_again(self):
        self.hours(fill="standard")
        timekeeping.confirm_period(self.root, self.p1, self.site1, SUN, SAT)
        self.assertEqual(timekeeping.reopen_period(self.root, self.p1, self.site1, SUN, SAT), 6)
        self.assertTrue(self.cell(MON).can_enter)
        with self.assertRaisesMessage(services.LaborError, "Nothing is confirmed"):
            timekeeping.reopen_period(self.root, self.p1, self.site1, SUN, SAT)

    def test_hours_on_a_day_that_stopped_being_worked_block_confirmation(self):
        self.hours(fill="standard")
        Attendance.objects.filter(employee=self.emp, date=MON).update(status="absent")
        sheet = self.tsheet()
        self.assertEqual(sheet.conflicts, 1)
        self.assertTrue(self.cell(MON).is_conflict)
        with self.assertRaisesMessage(services.LaborError, "not marked as worked"):
            timekeeping.confirm_period(self.root, self.p1, self.site1, SUN, SAT)

    def test_confirming_one_site_leaves_the_other_sites_hours_alone(self):
        deployment.allocate(self.emp, project=self.p2, location=self.site2, effective_from=date(2026, 3, 5))
        sitesheet.save_sheet(self.root, self.p2, self.site2, date(2026, 3, 5), SAT, {}, "present")
        self.hours(fill="standard")                                                    # Site One: 1-4 March
        timekeeping.save_hours(self.root, self.p2, self.site2, SUN, SAT, {}, "standard")  # Site Two: 5 and 7 March
        timekeeping.confirm_period(self.root, self.p1, self.site1, SUN, SAT)
        status = lambda d: TimeEntry.objects.get(employee=self.emp, date=d).status
        self.assertEqual((status(MON), status(date(2026, 3, 5))), ("confirmed", "draft"))

    def test_other_companies_see_nothing(self):
        from django.contrib.auth import get_user_model
        outsider = get_user_model().objects.create_user("out")
        sheet = timekeeping.build_timesheet(outsider, self.p1, self.site1, SUN, SAT)
        self.assertEqual(sheet.rows, [])


class ConstraintTests(TimeCase):
    def test_one_entry_per_worker_per_day_and_hours_within_a_day(self):
        self.hours({self.hkey(self.emp, MON): "8"})
        with self.assertRaises(IntegrityError), transaction.atomic():
            TimeEntry.objects.create(employee=self.emp, date=MON, project=self.p1, location=self.site1,
                                     hours=D("1"), expected_hours=D("8"))
        with self.assertRaises(IntegrityError), transaction.atomic():
            TimeEntry.objects.filter(employee=self.emp, date=MON).update(hours=D("0"))

    def test_parse_hours(self):
        self.assertEqual(timekeeping.parse_hours(" 7.5 "), D("7.5"))
        for bad in ("", "x", "nan", "inf", "0.2", "24.01", "1.234"):
            with self.assertRaises(services.LaborError, msg=bad):
                timekeeping.parse_hours(bad)
