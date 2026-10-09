"""Hours for a worker on several projects: each project's own overtime, shared workers, and the split tool."""
from datetime import date
from decimal import Decimal

from django.db import IntegrityError, transaction

from . import deployment, hours_split, services, sitesheet, timekeeping
from .models import LaborRate
from .test_sitesheet import FRI, SAT, SUN
from .test_timekeeping import MON, TUE, TimeCase
from .timesheet import TimeEntry

D = Decimal


class TwoProjectsCase(TimeCase):
    def setUp(self):
        super().setUp()
        deployment.join_project(self.emp, project=self.p2, effective_from=date(2026, 1, 5))   # Ravi is on both

    def okey(self, employee, d):
        return f"o_{employee.pk}_{d:%Y%m%d}"

    def hours2(self, posted=None, fill=""):
        return self.hours(posted, fill, project=self.p2, location=self.site2)

    def entry(self, d, project):
        return TimeEntry.objects.get(employee=self.emp, date=d, project=project)


class SheetTests(TwoProjectsCase):
    def test_a_worker_on_two_projects_gets_the_overtime_box_and_sees_the_other_hours(self):
        self.hours2({self.hkey(self.emp, MON): "4"})
        row = next(r for r in self.tsheet().rows if r.employee == self.emp)
        self.assertTrue(row.split)
        c = self.cell(MON)
        self.assertEqual((c.other_hours, c.other_regular, c.other_projects), (D("4"), D("4"), "P2"))
        self.assertEqual(c.ot_key, self.okey(self.emp, MON))

    def test_a_worker_on_one_project_is_unchanged(self):
        other = self.make_other("Imran")
        deployment.allocate(other, project=self.p1, location=self.site1, effective_from=date(2026, 1, 5))
        row = next(r for r in self.tsheet().rows if r.employee == other)
        self.assertFalse(row.split)

    def test_a_day_worked_for_the_other_project_is_not_missing_here(self):
        self.hours2({self.hkey(self.emp, MON): "8"})
        self.assertFalse(self.cell(MON).is_missing)           # P2 has the day
        self.assertTrue(self.cell(TUE).is_missing)            # nobody has it

    def test_fill_standard_goes_to_the_main_project_only(self):
        self.assertEqual(self.hours(fill="standard", project=self.p2, location=self.site2)["created"], 0)
        self.assertEqual(self.hours(fill="standard")["created"], 6)

    def test_clearing_one_project_leaves_the_other(self):
        self.hours2({self.hkey(self.emp, MON): "4"})
        self.hours({self.hkey(self.emp, MON): "4"})
        self.hours({self.hkey(self.emp, MON): ""})
        self.assertFalse(TimeEntry.objects.filter(employee=self.emp, date=MON, project=self.p1).exists())
        self.assertTrue(TimeEntry.objects.filter(employee=self.emp, date=MON, project=self.p2).exists())

    def test_the_database_allows_one_line_per_project_and_day(self):
        TimeEntry.objects.create(employee=self.emp, date=MON, project=self.p1, location=self.site1,
                                 hours=D("4"), expected_hours=D("8"))
        TimeEntry.objects.create(employee=self.emp, date=MON, project=self.p2, location=self.site2,
                                 hours=D("4"), expected_hours=D("8"))
        with self.assertRaises(IntegrityError), transaction.atomic():
            TimeEntry.objects.create(employee=self.emp, date=MON, project=self.p1, location=self.site1,
                                     hours=D("1"), expected_hours=D("8"))


class OvertimeTests(TwoProjectsCase):
    def test_each_project_keeps_its_own_overtime(self):
        self.hours2({self.hkey(self.emp, MON): "4"})
        r = self.hours({self.hkey(self.emp, MON): "6", self.okey(self.emp, MON): "2"})
        self.assertEqual(r["skipped"], [])
        a, b = self.entry(MON, self.p1), self.entry(MON, self.p2)
        self.assertEqual((a.hours, a.expected_hours, a.overtime_hours), (D("6"), D("4"), D("2")))
        self.assertEqual((b.hours, b.regular_hours, b.overtime_hours), (D("4"), D("4"), D("0")))

    def test_regular_hours_across_projects_cannot_pass_the_standard_day(self):
        self.hours2({self.hkey(self.emp, MON): "4"})
        r = self.hours({self.hkey(self.emp, MON): "6"})                 # no overtime named
        self.assertEqual(r["created"], 0)
        self.assertIn("more than the 8 a day allows", r["skipped"][0])
        self.assertFalse(TimeEntry.objects.filter(employee=self.emp, date=MON, project=self.p1).exists())

    def test_one_project_that_day_and_no_overtime_typed_works_as_before(self):
        self.hours({self.hkey(self.emp, MON): "10"})
        e = self.entry(MON, self.p1)
        self.assertEqual((e.hours, e.expected_hours, e.overtime_hours), (D("10"), D("8"), D("2")))

    def test_typed_overtime_is_checked(self):
        for bad in ("abc", "7", "-1", "1.234"):
            r = self.hours({self.hkey(self.emp, MON): "6", self.okey(self.emp, MON): bad})
            self.assertEqual((r["created"], len(r["skipped"])), (0, 1), bad)

    def test_a_worker_not_eligible_for_overtime_cannot_be_given_any(self):
        LaborRate.objects.filter(profile=self.worker1).update(overtime_eligible=False)
        r = self.hours({self.hkey(self.emp, MON): "6", self.okey(self.emp, MON): "2"})
        self.assertEqual(r["created"], 0)
        self.assertIn("not eligible", r["skipped"][0])

    def test_editing_a_line_changes_its_overtime_but_not_the_other_projects(self):
        self.hours2({self.hkey(self.emp, MON): "4"})
        self.hours({self.hkey(self.emp, MON): "6", self.okey(self.emp, MON): "2"})
        r = self.hours({self.hkey(self.emp, MON): "7", self.okey(self.emp, MON): "3"})
        self.assertEqual(r["updated"], 1)
        a, b = self.entry(MON, self.p1), self.entry(MON, self.p2)
        self.assertEqual((a.hours, a.overtime_hours, b.hours, b.overtime_hours), (D("7"), D("3"), D("4"), D("0")))


class SharedWorkerTests(TimeCase):
    def driver(self):
        driver = self.make_other("Ali")
        deployment.set_shared(driver.labor_profile, True)
        return driver

    def test_a_shared_worker_is_on_every_active_projects_sheets(self):
        driver = self.driver()
        for project, site in ((self.p1, self.site1), (self.p2, self.site2)):
            row = next(r for r in sitesheet.build_sheet(self.root, project, site, SUN, SAT).rows if r.employee == driver)
            self.assertTrue(all(c.covered for c in row.cells))
        self.p2.status = "closed"
        self.p2.save()
        rows = sitesheet.build_sheet(self.root, self.p2, self.site2, SUN, SAT).rows
        self.assertNotIn(driver, [r.employee for r in rows])

    def test_attendance_marked_once_shows_on_every_sheet(self):
        driver = self.driver()
        self.save(fill="present", project=self.p2, location=self.site2)           # marked on Project Two's sheet
        row = next(r for r in self.sheet().rows if r.employee == driver)          # seen on Project One's
        self.assertTrue(all(c.record is not None and not c.editable for c in row.cells if c.date != FRI))

    def test_a_shared_worker_has_the_overtime_box_but_is_not_filled_automatically(self):
        driver = self.driver()
        self.save(fill="present", project=self.p2, location=self.site2)
        row = next(r for r in self.tsheet().rows if r.employee == driver)
        self.assertTrue(row.split)
        self.hours(fill="standard")
        self.assertFalse(TimeEntry.objects.filter(employee=driver).exists())


class SplitDaysTests(TwoProjectsCase):
    def lines(self, a=("4", "2"), b=("4", "0")):
        return [{"project": self.p1, "regular": D(a[0]), "overtime": D(a[1])},
                {"project": self.p2, "regular": D(b[0]), "overtime": D(b[1])}]

    def test_a_week_divided_with_overtime_on_one_project(self):
        r = hours_split.split_days(self.root, self.emp, self.lines(), SUN, SAT)
        self.assertEqual((r["days"], r["lines"], r["skipped"]), (6, 12, []))
        one = TimeEntry.objects.filter(employee=self.emp, project=self.p1)
        two = TimeEntry.objects.filter(employee=self.emp, project=self.p2)
        self.assertEqual({(e.hours, e.expected_hours) for e in one}, {(D("6"), D("4"))})
        self.assertEqual({(e.hours, e.expected_hours) for e in two}, {(D("4"), D("4"))})
        self.assertEqual((sum(e.overtime_hours for e in one), sum(e.overtime_hours for e in two)), (D("12"), D("0")))

    def test_it_replaces_drafts_and_skips_confirmed_days(self):
        hours_split.split_days(self.root, self.emp, self.lines(), SUN, SAT)
        hours_split.split_days(self.root, self.emp, self.lines(("3", "0"), ("5", "0")), MON, TUE)
        self.assertEqual((self.entry(MON, self.p1).hours, self.entry(MON, self.p2).hours), (D("3"), D("5")))
        self.assertEqual(self.entry(SUN, self.p1).hours, D("6"))                        # outside the range: untouched
        timekeeping.confirm_period(self.root, self.p1, self.site1, SUN, SAT)
        again = hours_split.split_days(self.root, self.emp, self.lines(("2", "0"), ("2", "0")), SUN, SAT)
        self.assertEqual((again["days"], len(again["skipped"])), (0, 6))
        self.assertIn("confirmed", again["skipped"][0])
        self.assertEqual(self.entry(MON, self.p1).hours, D("3"))

    def test_rules_and_errors(self):
        from apps.organization.models import Project
        elsewhere = Project.objects.create(company=self.co, code="P3", name="Not his", location=self.site1)
        with self.assertRaisesMessage(services.LaborError, "is not on P3"):
            hours_split.split_days(self.root, self.emp, [{"project": elsewhere, "regular": D("8")}], SUN, SAT)
        with self.assertRaisesMessage(services.LaborError, "Choose at least one project"):
            hours_split.split_days(self.root, self.emp, [], SUN, SAT)
        with self.assertRaisesMessage(services.LaborError, "Enter the hours"):
            hours_split.split_days(self.root, self.emp, self.lines(("0", "0"), ("0", "0")), SUN, SAT)
        with self.assertRaisesMessage(services.LaborError, "cannot be before"):
            hours_split.split_days(self.root, self.emp, self.lines(), SAT, SUN)
        r = hours_split.split_days(self.root, self.emp, self.lines(("6", "0"), ("6", "0")), SUN, SAT)   # 12 > 8
        self.assertEqual((r["days"], len(r["skipped"])), (0, 6))
        self.assertIn("more than the 8", r["skipped"][0])

    def test_a_half_day_cannot_take_a_full_days_regular_hours(self):
        other = self.make_other("Imran")
        deployment.allocate(other, project=self.p1, location=self.site1, effective_from=date(2026, 1, 5))
        deployment.join_project(other, project=self.p2, effective_from=date(2026, 1, 5))
        self.save({self.key(other, SUN): "half_day"})
        lines = [{"project": self.p1, "regular": D("2"), "overtime": D("0")},
                 {"project": self.p2, "regular": D("2"), "overtime": D("0")}]
        r = hours_split.split_days(self.root, other, lines, SUN, SUN)
        self.assertEqual((r["days"], r["lines"]), (1, 2))                         # 2 + 2 fits the half day's 4
        too_much = [dict(line, regular=D("3")) for line in lines]
        r = hours_split.split_days(self.root, other, too_much, SUN, SUN)
        self.assertEqual(r["days"], 0)
        self.assertIn("more than the 4", r["skipped"][0])


class EvenShareTests(TimeCase):
    def test_shares_are_quarter_hours_and_add_up(self):
        self.assertEqual(hours_split.even_shares(D("8"), 2), [D("4"), D("4")])
        self.assertEqual(hours_split.even_shares(D("8"), 3), [D("3.00"), D("2.50"), D("2.50")])
        self.assertEqual(hours_split.even_shares(D("8"), 5), [D("2.00")] + [D("1.50")] * 4)
        self.assertEqual(hours_split.even_shares(D("4"), 5), [D("1.00")] + [D("0.75")] * 4)
        for total, count in ((D("8"), 4), (D("8"), 7), (D("4"), 3)):
            self.assertEqual(sum(hours_split.even_shares(total, count)), total)

    def test_a_shared_worker_is_divided_evenly_and_can_have_overtime_on_one_project(self):
        driver = self.make_other("Ali")
        deployment.set_shared(driver.labor_profile, True)
        self.save(fill="present")                                                   # the driver is marked on the sheet
        lines = [{"project": self.p1, "overtime": D("3")}, {"project": self.p2}]
        r = hours_split.split_days(self.root, driver, lines, SUN, SAT, even=True)
        self.assertEqual((r["days"], r["lines"], r["skipped"]), (6, 12, []))
        one = TimeEntry.objects.filter(employee=driver, project=self.p1)
        two = TimeEntry.objects.filter(employee=driver, project=self.p2)
        self.assertEqual({(e.hours, e.expected_hours) for e in one}, {(D("7"), D("4"))})      # 4 regular + 3 overtime
        self.assertEqual({(e.hours, e.expected_hours) for e in two}, {(D("4"), D("4"))})
        self.assertEqual((sum(e.overtime_hours for e in one), sum(e.overtime_hours for e in two)), (D("18"), D("0")))
