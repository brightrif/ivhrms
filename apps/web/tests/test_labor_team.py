from datetime import date

from apps.labor import deployment
from apps.labor.allocation import LaborAllocation

from .test_labor_sites import SitePageCase

MAR1 = date(2026, 3, 1)


class TeamPageTests(SitePageCase):
    def team(self, project=None):
        return self.url("labor_team", (project or self.p1).pk)

    def post(self, project=None, **data):
        return self.client.post(self.team(project), data)

    def test_the_page_lists_the_team_and_the_shared_workers(self):
        deployment.join_project(self.p.employee, project=self.p1, effective_from=MAR1)
        driver = self.worker("Ali")
        deployment.set_shared(driver, True)
        page = self.client.get(self.team())
        self.assertContains(page, "Ravi")
        self.assertContains(page, "Ali")
        self.assertContains(page, "Every active project")

    def test_adding_ticked_workers_with_a_past_start_date(self):
        imran = self.worker("Imran")
        r = self.post(action="add", profiles=[self.p.pk, imran.pk], effective_from="2026-03-01")
        self.assertRedirects(r, self.team())
        rows = LaborAllocation.objects.filter(project=self.p1, effective_to__isnull=True)
        self.assertEqual(rows.count(), 2)
        self.assertEqual({a.effective_from for a in rows}, {MAR1})

    def test_a_worker_can_be_on_two_projects_and_each_team_says_so(self):
        self.post(action="add", profiles=[self.p.pk], effective_from="2026-03-01")
        self.post(self.p2, action="add", profiles=[self.p.pk], effective_from="2026-03-02")
        self.assertEqual(len(deployment.open_allocations(self.p.employee)), 2)
        self.assertContains(self.client.get(self.team()), "also on P2")
        self.assertContains(self.client.get(self.team(self.p2)), "also on P1")

    def test_removing_ends_only_that_project_on_the_chosen_day(self):
        self.post(action="add", profiles=[self.p.pk], effective_from="2026-03-01")
        self.post(self.p2, action="add", profiles=[self.p.pk], effective_from="2026-03-01")
        a = LaborAllocation.objects.get(employee=self.p.employee, project=self.p1)
        self.post(action="remove", allocation=a.pk, last_day="2026-03-10")
        a.refresh_from_db()
        self.assertEqual(a.effective_to, date(2026, 3, 10))
        self.assertEqual([x.project for x in deployment.open_allocations(self.p.employee)], [self.p2])

    def test_a_rule_error_is_shown_not_raised(self):
        self.post(action="add", profiles=[self.p.pk], effective_from="2026-03-05")
        a = LaborAllocation.objects.get(employee=self.p.employee)
        r = self.client.post(self.team(), {"action": "remove", "allocation": a.pk, "last_day": "2026-03-01"}, follow=True)
        self.assertContains(r, "cannot be before")
        a.refresh_from_db()
        self.assertIsNone(a.effective_to)

    def test_making_a_project_the_main_one(self):
        self.post(action="add", profiles=[self.p.pk], effective_from="2026-03-01")
        self.post(self.p2, action="add", profiles=[self.p.pk], effective_from="2026-03-01")
        b = LaborAllocation.objects.get(employee=self.p.employee, project=self.p2)
        self.post(self.p2, action="main", allocation=b.pk)
        self.assertEqual(deployment.open_allocation(self.p.employee), b)

    def test_a_worker_can_be_made_shared_and_stop_being_shared(self):
        driver = self.worker("Ali")
        self.post(share=driver.pk)
        driver.refresh_from_db()
        self.assertTrue(driver.serves_all_projects)
        self.post(unshare=driver.pk)
        driver.refresh_from_db()
        self.assertFalse(driver.serves_all_projects)

    def test_search_finds_workers_not_yet_on_the_project(self):
        self.worker("Imran")
        page = self.client.get(self.team(), {"q": "Rav"})
        self.assertContains(page, "Ravi")
        self.assertNotContains(page, "Imran")

    def test_people_who_can_only_look_cannot_change_the_team(self):
        for user in (self.finance, self.boss):
            self.client.force_login(user)
            self.assertEqual(self.client.get(self.team()).status_code, 200, user.username)
            self.assertEqual(self.post(action="add", profiles=[self.p.pk], effective_from="2026-03-01").status_code,
                             403, user.username)
            self.assertEqual(self.post(share=self.p.pk).status_code, 403, user.username)
        self.assertEqual(LaborAllocation.objects.count(), 0)

    def test_other_companies_projects_are_out_of_reach(self):
        self.assertEqual(self.client.get(self.url("labor_team", self.foreign.pk)).status_code, 404)
