from datetime import date
from decimal import Decimal
from urllib.parse import urlencode

from apps.labor import deployment
from apps.labor.timesheet import TimeEntry

from .test_labor_timesheet import MON, TimesheetPageCase

D = Decimal


class SplitPageCase(TimesheetPageCase):
    def split_url(self, **q):
        return self.url("labor_split") + "?" + urlencode(q)

    def join_second_project(self):
        deployment.join_project(self.p.employee, project=self.p2, effective_from=date(2026, 1, 5))


class TimesheetBoxTests(SplitPageCase):
    def test_no_overtime_box_for_a_worker_on_one_project(self):
        self.assertNotContains(self.page(), f'name="o_{self.p.employee_id}_')

    def test_a_worker_on_two_projects_gets_the_box_and_the_split_link(self):
        self.join_second_project()
        page = self.page()
        self.assertContains(page, f'name="o_{self.p.employee_id}_20260302"')
        self.assertContains(page, self.url("labor_split"))

    def test_hours_with_overtime_are_saved_through_the_page(self):
        self.join_second_project()
        self.post(**{self.key(MON): "6", f"o_{self.p.employee_id}_20260302": "2"})
        e = TimeEntry.objects.get(employee=self.p.employee, date=MON)
        self.assertEqual((e.hours, e.expected_hours, e.overtime_hours), (D("6"), D("4"), D("2")))

    def test_a_rule_error_is_shown_not_raised(self):
        self.join_second_project()
        self.post(**{self.key(MON): "6"})                                   # P1 alone that day: standard day, overtime 0
        r = self.post(**{self.key(MON): "7", f"o_{self.p.employee_id}_20260302": "x"})
        self.assertContains(self.landing(r), "Not saved")


class SplitPageTests(SplitPageCase):
    def setUp(self):
        super().setUp()
        self.join_second_project()

    def test_the_page_lists_workers_on_several_projects(self):
        page = self.client.get(self.split_url(first="2026-03-01", last="2026-03-07"))
        self.assertContains(page, "Ravi")
        self.assertContains(page, "Split hours")

    def test_a_worker_page_lists_the_projects(self):
        page = self.client.get(self.split_url(worker=self.p.pk, first="2026-03-01", last="2026-03-07"))
        self.assertContains(page, "P1")
        self.assertContains(page, "P2")
        self.assertContains(page, "Apply to the days")

    def test_applying_a_split(self):
        data = {"worker": self.p.pk, "first": "2026-03-01", "last": "2026-03-07", "mode": "typed",
                f"inc_{self.p1.pk}": "1", f"reg_{self.p1.pk}": "4", f"ot_{self.p1.pk}": "2",
                f"inc_{self.p2.pk}": "1", f"reg_{self.p2.pk}": "4"}
        r = self.client.post(self.url("labor_split"), data)
        self.assertContains(self.landing(r), "6 days divided")
        one = TimeEntry.objects.filter(employee=self.p.employee, project=self.p1)
        self.assertEqual({(e.hours, e.expected_hours) for e in one}, {(D("6"), D("4"))})
        self.assertEqual(TimeEntry.objects.filter(employee=self.p.employee, project=self.p2).count(), 6)

    def test_a_mistake_is_explained(self):
        data = {"worker": self.p.pk, "first": "2026-03-01", "last": "2026-03-07",
                f"inc_{self.p1.pk}": "1", f"reg_{self.p1.pk}": "nope"}
        r = self.client.post(self.url("labor_split"), data)
        self.assertContains(self.landing(r), "is not a number of hours")
        self.assertFalse(TimeEntry.objects.exists())

    def test_people_who_can_only_look_cannot_apply(self):
        data = {"worker": self.p.pk, "first": "2026-03-01", "last": "2026-03-07",
                f"inc_{self.p1.pk}": "1", f"reg_{self.p1.pk}": "8"}
        for user in (self.finance, self.boss):
            self.client.force_login(user)
            self.assertEqual(self.client.get(self.split_url(worker=self.p.pk)).status_code, 200, user.username)
            self.assertEqual(self.client.post(self.url("labor_split"), data).status_code, 403, user.username)
        self.assertFalse(TimeEntry.objects.exists())

    def test_needs_login_and_permission(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.split_url()).status_code, 302)
        self.client.force_login(self.nobody)
        self.assertEqual(self.client.get(self.split_url()).status_code, 403)

    def test_other_companies_workers_are_out_of_reach(self):
        theirs = self.worker("Theirs", company=self.other_co)
        self.assertEqual(self.client.get(self.split_url(worker=theirs.pk)).status_code, 404)
