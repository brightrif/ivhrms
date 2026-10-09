"""The cost and manpower reports for a worker on several projects: each day is divided, never charged twice."""
from datetime import date
from decimal import Decimal

from apps.employees.models import Employee

from . import deployment, hours_split, reporting, services
from .test_sitesheet import SAT, SUN
from .test_timekeeping import MON, TimeCase

D = Decimal
JAN5 = date(2026, 1, 5)


class CostCase(TimeCase):
    """Ravi (5.000 a day) worked 1-4, 5 and 7 March, six days, on P1 / Site One."""

    def facts(self, first=SUN, last=SAT):
        return reporting.collect_facts(self.root, first, last)[0]

    def sums(self, facts, employee, project):
        mine = [f for f in facts.values() if f.employee == employee and f.project == project]
        return tuple(sum((getattr(f, name) for f in mine), D("0")) for name in ("days", "wages", "hours"))

    def two_projects(self):
        deployment.join_project(self.emp, project=self.p2, effective_from=JAN5)

    def worker(self, name, wage_basis, rate):
        e = Employee(company=self.co, first_name=name, joining_date=date(2026, 1, 1))
        return services.create_labor_worker(e, engagement="direct", trade=self.mason, wage_basis=wage_basis,
                                            rate=rate).employee

    def shared_driver(self, wage_basis="daily", rate=D("5")):
        driver = self.worker("Ali", wage_basis, rate)
        deployment.set_shared(driver.labor_profile, True)
        return driver


class DailyWageTests(CostCase):
    def test_a_worker_on_one_project_is_charged_exactly_as_before(self):
        facts = self.facts()
        self.assertEqual(len(facts), 1)
        self.assertEqual(self.sums(facts, self.emp, self.p1)[:2], (D("6"), D("30")))

    def test_a_worker_on_two_projects_is_charged_once_and_split_equally_until_hours_exist(self):
        self.two_projects()
        facts = self.facts()
        self.assertEqual(self.sums(facts, self.emp, self.p1)[:2], (D("3"), D("15")))
        self.assertEqual(self.sums(facts, self.emp, self.p2)[:2], (D("3"), D("15")))

    def test_the_split_follows_the_regular_hours(self):
        self.two_projects()
        lines = [{"project": self.p1, "regular": D("6")}, {"project": self.p2, "regular": D("2")}]
        hours_split.split_days(self.root, self.emp, lines, SUN, SAT)
        facts = self.facts()
        self.assertEqual(self.sums(facts, self.emp, self.p1)[:2], (D("4.5"), D("22.5")))
        self.assertEqual(self.sums(facts, self.emp, self.p2)[:2], (D("1.5"), D("7.5")))

    def test_overtime_hours_do_not_change_how_the_wage_is_split(self):
        self.two_projects()
        lines = [{"project": self.p1, "regular": D("4"), "overtime": D("4")}, {"project": self.p2, "regular": D("4")}]
        hours_split.split_days(self.root, self.emp, lines, SUN, SAT)
        facts = self.facts()
        one, two = self.sums(facts, self.emp, self.p1), self.sums(facts, self.emp, self.p2)
        self.assertEqual((one[1], two[1]), (D("15"), D("15")))              # the wage is split by regular hours
        self.assertEqual((one[2], two[2]), (D("48"), D("24")))              # the hours show each project's own overtime

    def test_a_day_with_hours_on_one_project_only_belongs_to_that_project(self):
        self.two_projects()
        self.hours({self.hkey(self.emp, MON): "8"})                         # Monday: P1 alone
        facts = self.facts()
        self.assertEqual(self.sums(facts, self.emp, self.p1)[:2], (D("3.5"), D("17.5")))   # Monday + half of five days
        self.assertEqual(self.sums(facts, self.emp, self.p2)[:2], (D("2.5"), D("12.5")))


class MonthlySalaryTests(CostCase):
    def test_a_monthly_salary_is_charged_once_on_one_project_and_divided_on_two(self):
        imran = self.worker("Imran", "monthly", D("300"))
        deployment.allocate(imran, project=self.p1, location=self.site1, effective_from=JAN5)
        facts = self.facts()
        self.assertEqual(self.sums(facts, imran, self.p1)[1], D("70"))       # 7 calendar days x 300 / 30
        deployment.join_project(imran, project=self.p2, effective_from=JAN5)
        facts = self.facts()
        self.assertEqual(self.sums(facts, imran, self.p1)[1], D("35"))
        self.assertEqual(self.sums(facts, imran, self.p2)[1], D("35"))


class SharedWorkerTests(CostCase):
    def test_a_shared_driver_is_divided_between_the_active_projects(self):
        driver = self.shared_driver()
        self.save(fill="present")                                            # present Sun-Thu and Sat
        facts = self.facts()
        self.assertEqual(self.sums(facts, driver, self.p1)[:2], (D("3"), D("15")))
        self.assertEqual(self.sums(facts, driver, self.p2)[:2], (D("3"), D("15")))
        self.p2.status = "closed"
        self.p2.save()
        facts = self.facts()
        self.assertEqual(self.sums(facts, driver, self.p1)[:2], (D("6"), D("30")))
        self.assertEqual(self.sums(facts, driver, self.p2)[:2], (D("0"), D("0")))

    def test_a_shared_driver_with_hours_follows_them(self):
        driver = self.shared_driver()
        self.save(fill="present")
        hours_split.split_days(self.root, driver, [{"project": self.p1, "overtime": D("3")}, {"project": self.p2}],
                               SUN, SAT, even=True)
        facts = self.facts()
        self.assertEqual(self.sums(facts, driver, self.p1)[1], D("15"))
        self.assertEqual(self.sums(facts, driver, self.p2)[1], D("15"))
        self.assertEqual(self.sums(facts, driver, self.p1)[2], D("42"))      # 7 hours a day: 4 regular + 3 overtime

    def test_a_shared_monthly_worker_is_divided_too(self):
        driver = self.shared_driver("monthly", D("300"))
        facts = self.facts()
        self.assertEqual(self.sums(facts, driver, self.p1)[1], D("35"))
        self.assertEqual(self.sums(facts, driver, self.p2)[1], D("35"))


class ReportTests(CostCase):
    def test_the_cost_report_total_counts_each_worker_and_each_wage_once(self):
        driver = self.shared_driver()
        self.two_projects()
        self.save(fill="present")
        report = reporting.cost_report(self.root, SUN, SAT)
        table = report.tables[0]
        self.assertEqual(table.total[3], 2)                                  # two people
        self.assertEqual(table.total[6], D("60"))                            # 30 + 30, not more
        self.assertEqual({row[0]: row[3] for row in table.rows}, {"P1": 2, "P2": 2})   # each is on both projects
        self.assertTrue(any("more than one project" in note for note in report.notes))

    def test_manpower_counts_a_worker_on_each_project_he_worked_for(self):
        driver = self.shared_driver()
        self.two_projects()
        self.save(fill="present")
        table = reporting.manpower_report(self.root, SUN, SAT).tables[0]
        rows = {row[0]: row for row in table.rows}
        self.assertEqual(set(rows), {"P1 / Site One", "P2 / Site Two"})
        for row in rows.values():
            self.assertEqual(row[1], 2)                                      # Sunday: Ravi and the driver
            self.assertEqual(row[-1], D("6"))                                # 6 man-days: half of each person's six
