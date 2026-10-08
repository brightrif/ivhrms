from datetime import date, timedelta
from decimal import Decimal

from django.utils import timezone

from apps.attendance.models import Attendance
from apps.audit.models import AuditEvent
from apps.scheduling.models import Holiday

from . import deployment, sitesheet
from .services import LaborError
from .test_deployment import DeploymentCase

SUN = date(2026, 3, 1)                       # a Sunday; Friday 6 March is the weekly off
FRI, SAT = date(2026, 3, 6), date(2026, 3, 7)


class SheetCase(DeploymentCase):
    def setUp(self):
        super().setUp()
        from django.contrib.auth import get_user_model
        self.root = get_user_model().objects.create_user("root", is_superuser=True)
        self.place(on=date(2026, 1, 5))                                   # Ravi is on P1 / Site One from 5 Jan

    def sheet(self, first=SUN, last=SAT, project=None, location=None):
        return sitesheet.build_sheet(self.root, project or self.p1, location or self.site1, first, last)

    def save(self, posted=None, fill="", first=SUN, last=SAT, project=None, location=None):
        return sitesheet.save_sheet(self.root, project or self.p1, location or self.site1, first, last, posted or {}, fill)

    def key(self, employee, d):
        return f"c_{employee.pk}_{d:%Y%m%d}"


class PeriodTests(SheetCase):
    def test_day_week_and_month_bounds(self):
        self.assertEqual(sitesheet.period_bounds("day", date(2026, 3, 11)), (date(2026, 3, 11),) * 2)
        self.assertEqual(sitesheet.period_bounds("week", date(2026, 3, 11)), (date(2026, 3, 8), date(2026, 3, 14)))
        self.assertEqual(sitesheet.period_bounds("week", date(2026, 3, 8)), (date(2026, 3, 8), date(2026, 3, 14)))   # a Sunday starts its own week
        self.assertEqual(sitesheet.period_bounds("week", date(2026, 3, 14)), (date(2026, 3, 8), date(2026, 3, 14)))
        self.assertEqual(sitesheet.period_bounds("month", date(2026, 2, 17)), (date(2026, 2, 1), date(2026, 2, 28)))
        self.assertEqual(sitesheet.period_bounds("month", date(2028, 2, 17)), (date(2028, 2, 1), date(2028, 2, 29)))

    def test_previous_and_next(self):
        a = date(2026, 3, 11)
        self.assertEqual(sitesheet.shift_anchor("day", a, -1), date(2026, 3, 10))
        self.assertEqual(sitesheet.shift_anchor("week", a, 1), date(2026, 3, 18))
        self.assertEqual(sitesheet.shift_anchor("month", date(2026, 1, 20), -1), date(2025, 12, 1))
        self.assertEqual(sitesheet.shift_anchor("month", date(2026, 12, 20), 1), date(2027, 1, 1))

    def test_sites_in_period_lists_each_site_that_had_workers(self):
        self.place(project=self.p2, location=self.site2, on=date(2026, 3, 4))      # moved mid-week
        pairs = sitesheet.sites_in_period(self.root, SUN, SAT)
        self.assertEqual([(p.code, l.code) for p, l in pairs], [("P1", "S1"), ("P2", "S2")])
        later = sitesheet.sites_in_period(self.root, date(2026, 3, 8), date(2026, 3, 14))
        self.assertEqual([p.code for p, l in later], ["P2"])


class BuildSheetTests(SheetCase):
    def test_one_row_per_worker_with_a_cell_for_every_day(self):
        sheet = self.sheet()
        self.assertEqual([r.employee for r in sheet.rows], [self.emp])
        self.assertEqual(len(sheet.rows[0].cells), 7)
        self.assertTrue(all(c.editable for c in sheet.rows[0].cells))

    def test_days_outside_the_allocation_are_not_editable_and_a_move_splits_the_sheet(self):
        deployment.allocate(self.emp, project=self.p2, location=self.site2, effective_from=date(2026, 3, 4))
        first = {c.date.day: c for c in self.sheet().rows[0].cells}
        second = {c.date.day: c for c in self.sheet(project=self.p2, location=self.site2).rows[0].cells}
        self.assertTrue(first[3].editable and not first[4].covered)                  # Site One until the 3rd
        self.assertTrue(second[4].editable and not second[3].covered)                # Site Two from the 4th

    def test_before_joining_and_future_days_are_not_editable(self):
        early = self.make_other("Early")                                            # joined 1 Jan 2026
        deployment.allocate(early, project=self.p1, location=self.site1, effective_from=date(2026, 1, 1))
        from apps.employees.models import Employee
        Employee.objects.filter(pk=early.pk).update(joining_date=date(2026, 3, 3))
        row = next(r for r in self.sheet().rows if r.employee.pk == early.pk)
        self.assertEqual([c.editable for c in row.cells], [False, False, True, True, True, True, True])
        today = timezone.localdate()
        row = next(r for r in sitesheet.build_sheet(self.root, self.p1, self.site1, today - timedelta(days=1),
                                                   today + timedelta(days=2)).rows if r.employee == self.emp)
        self.assertEqual([c.editable for c in row.cells], [True, True, False, False])

    def test_saved_days_are_shown_not_editable_and_counted(self):
        self.save({self.key(self.emp, SUN): "present", self.key(self.emp, date(2026, 3, 2)): "half_day",
                   self.key(self.emp, date(2026, 3, 3)): "absent"})
        row = self.sheet().rows[0]
        self.assertEqual([c.short for c in row.cells[:3]], ["P", "\u00bd", "A"])
        self.assertFalse(any(c.editable for c in row.cells[:3]))
        self.assertEqual((row.worked, row.absent), (Decimal("1.5"), 1))
        self.assertEqual(row.missing, 3)               # 4th, 5th and 7th: the Friday weekly off is not missing

    def test_weekly_offs_and_holidays_are_marked(self):
        Holiday.objects.create(date=date(2026, 3, 3), name="Test holiday")
        kinds = {c.date.day: c.kind for c in self.sheet().rows[0].cells}
        self.assertEqual((kinds[6], kinds[3], kinds[2]), ("weekly_off", "holiday", "working"))


class SaveSheetTests(SheetCase):
    def test_saves_what_was_entered_through_the_attendance_service(self):
        result = self.save({self.key(self.emp, SUN): "present", self.key(self.emp, date(2026, 3, 2)): "absent"})
        self.assertEqual(dict(result["created"]), {"present": 1, "absent": 1})
        rec = Attendance.objects.get(employee=self.emp, date=SUN)
        self.assertEqual((rec.status, rec.project, rec.location, rec.source), ("present", self.p1, self.site1, "bulk"))
        self.assertIn("Site sheet P1 / Site One", rec.remarks)
        self.assertEqual(rec.company_id, self.co.pk)

    def test_fill_marks_empty_working_days_but_never_weekly_offs_or_holidays(self):
        Holiday.objects.create(date=date(2026, 3, 3), name="Test holiday")
        result = self.save(fill="present")
        days = set(Attendance.objects.filter(employee=self.emp).values_list("date", flat=True))
        self.assertEqual(days, {date(2026, 3, d) for d in (1, 2, 4, 5, 7)})          # not Fri 6th, not the holiday 3rd
        self.assertEqual(result["created"]["present"], 5)

    def test_typed_exceptions_win_over_the_fill_and_an_off_day_can_be_worked(self):
        self.save({self.key(self.emp, date(2026, 3, 2)): "absent", self.key(self.emp, FRI): "present"}, fill="present")
        status = dict(Attendance.objects.filter(employee=self.emp).values_list("date", "status"))
        self.assertEqual(status[date(2026, 3, 2)], "absent")
        self.assertEqual(status[FRI], "present")                       # worked on their rest day: it is recorded

    def test_saving_twice_never_overwrites_or_duplicates(self):
        self.save({self.key(self.emp, SUN): "present"})
        again = self.save({self.key(self.emp, SUN): "absent"}, fill="present")
        self.assertEqual(Attendance.objects.get(employee=self.emp, date=SUN).status, "present")     # untouched
        self.assertEqual(Attendance.objects.filter(employee=self.emp).count(), 6)                   # + the 5 working days
        self.assertEqual(self.save(fill="present")["created"], {})
        self.assertEqual(again["skipped"], [])

    def test_only_cells_the_server_offers_are_read(self):
        other = self.make_other("Imran")
        deployment.allocate(other, project=self.p2, location=self.site2, effective_from=date(2026, 1, 5))
        future = timezone.localdate() + timedelta(days=2)
        deployment.allocate(self.emp, project=self.p2, location=self.site2, effective_from=date(2026, 3, 4))
        self.save({self.key(other, SUN): "present",                      # a worker who is on another site
                   self.key(self.emp, date(2026, 3, 5)): "present",      # a day after this worker moved away
                   self.key(self.emp, future): "present",                # a future day
                   "c_999999_20260301": "present", self.key(self.emp, SUN): "bogus"})
        self.assertEqual(Attendance.objects.count(), 0)

    def test_fill_must_be_one_of_the_entry_statuses(self):
        for bad in ("holiday", "paid_leave", "nonsense"):
            with self.assertRaises(LaborError):
                self.save(fill=bad)
        self.assertEqual(Attendance.objects.count(), 0)

    def test_every_saved_day_is_audited_against_the_worker(self):
        before = AuditEvent.objects.filter(module="attendance", subject_employee_id=self.emp.pk).count()
        self.save(fill="present")
        after = AuditEvent.objects.filter(module="attendance", subject_employee_id=self.emp.pk, action="create").count()
        self.assertEqual(after - before, 6)           # Sunday to Thursday and Saturday

    def test_a_month_in_one_go(self):
        feb = (date(2026, 2, 1), date(2026, 2, 28))
        result = self.save(fill="present", first=feb[0], last=feb[1])
        self.assertEqual(result["created"]["present"], 24)               # 28 days minus 4 Fridays
        self.assertEqual(Attendance.objects.filter(employee=self.emp).count(), 24)
