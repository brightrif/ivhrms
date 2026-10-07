from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model

from apps.employees.models import Employee
from apps.labor import deployment, services
from apps.labor.allocation import LaborAllocation, WorkOrder
from apps.labor.models import LaborProfile
from apps.organization.models import Location, Project

from .test_labor import LaborPageCase

D = Decimal


class SitePageCase(LaborPageCase):
    def setUp(self):
        super().setUp()
        self.site1 = Location.objects.create(code="S1", name="Site One", is_site=True)
        self.site2 = Location.objects.create(code="S2", name="Site Two", is_site=True)
        self.office = Location.objects.create(code="HQ", name="Head Office", is_site=False)
        self.p1 = Project.objects.create(company=self.co, code="P1", name="Tower A", location=self.site1)
        self.p2 = Project.objects.create(company=self.co, code="P2", name="Villa B", location=self.site2)
        self.nowhere = Project.objects.create(company=self.co, code="P3", name="No site yet")
        self.foreign = Project.objects.create(company=self.other_co, code="X1", name="Foreign", location=self.site1)
        self.p = self.worker("Ravi")

    def allocate_post(self, profile=None, **over):
        data = {"project": self.p1.pk, "location": "", "work_order": "", "effective_from": "2026-03-01", "notes": ""}
        data.update(over)
        return self.client.post(self.url("labor_allocate", (profile or self.p).pk), data)

    def place(self, profile=None, project=None, location=None, on=date(2026, 3, 1), work_order=None):
        profile = profile or self.p
        return deployment.allocate(profile.employee, project=project or self.p1, location=location or self.site1,
                                   work_order=work_order, effective_from=on)


class AccessTests(SitePageCase):
    def test_pages_need_the_right_permission(self):
        wo = WorkOrder.objects.create(project=self.p1, code="WO-1", name="A")
        self.place()
        pages = [self.url(n) for n in ("labor_site_list", "labor_work_order_list", "labor_work_order_create")]
        pages += [self.url("labor_site_detail", self.p1.pk, self.site1.pk), self.url("labor_allocate", self.p.pk),
                  self.url("labor_release", self.p.pk), self.url("labor_transfer") + f"?profiles={self.p.pk}",
                  self.url("labor_work_order_edit", wo.pk)]
        self.client.logout()
        for url in pages:
            self.assertEqual(self.client.get(url).status_code, 302, url)
        self.client.force_login(self.nobody)
        for url in pages:
            self.assertEqual(self.client.get(url).status_code, 403, url)

    def test_finance_and_management_can_look_but_not_change(self):
        wo = WorkOrder.objects.create(project=self.p1, code="WO-1", name="A")
        self.place()
        for user in (self.finance, self.boss):
            self.client.force_login(user)
            for url in (self.url("labor_site_list"), self.url("labor_work_order_list"),
                        self.url("labor_site_detail", self.p1.pk, self.site1.pk)):
                self.assertEqual(self.client.get(url).status_code, 200, (user.username, url))
            for url in (self.url("labor_allocate", self.p.pk), self.url("labor_release", self.p.pk),
                        self.url("labor_transfer") + f"?profiles={self.p.pk}", self.url("labor_work_order_create"),
                        self.url("labor_work_order_edit", wo.pk)):
                self.assertEqual(self.client.get(url).status_code, 403, (user.username, url))
            self.assertEqual(self.client.post(self.url("labor_work_order_toggle", wo.pk)).status_code, 403)
            page = self.client.get(self.url("labor_list")).content.decode()
            self.assertNotIn('name="profiles"', page)                 # no tick boxes for people who cannot move anyone

    def test_other_companies_projects_and_workers_are_out_of_reach(self):
        theirs = self.worker("Theirs", company=self.other_co)
        self.assertEqual(self.client.get(self.url("labor_site_detail", self.foreign.pk, self.site1.pk)).status_code, 404)
        self.assertEqual(self.client.get(self.url("labor_allocate", theirs.pk)).status_code, 404)
        self.assertEqual(self.client.get(self.url("labor_release", theirs.pk)).status_code, 404)
        wo = WorkOrder.objects.create(project=self.foreign, code="WO-F", name="Foreign work")
        self.assertEqual(self.client.get(self.url("labor_work_order_edit", wo.pk)).status_code, 404)
        self.assertNotContains(self.client.get(self.url("labor_work_order_list")), "WO-F")
        self.assertNotContains(self.client.get(self.url("labor_site_list")), "Foreign")


class AllocatePageTests(SitePageCase):
    def test_allocate_a_worker_and_see_it_everywhere(self):
        r = self.allocate_post()
        self.assertRedirects(r, self.url("labor_detail", self.p.pk))
        a = deployment.open_allocation(self.p.employee)
        self.assertEqual((a.project, a.location), (self.p1, self.site1))                    # the project's own site
        self.assertContains(self.client.get(self.url("labor_detail", self.p.pk)), "Tower A")
        self.assertContains(self.client.get(self.url("labor_list")), "Site One")
        self.assertContains(self.client.get(self.url("labor_site_detail", self.p1.pk, self.site1.pk)), "Ravi")

    def test_a_chosen_site_overrides_the_projects_own(self):
        self.allocate_post(location=self.site2.pk)
        self.assertEqual(deployment.open_allocation(self.p.employee).location, self.site2)

    def test_project_without_a_site_needs_one_chosen(self):
        r = self.allocate_post(project=self.nowhere.pk)
        self.assertContains(r, "has no site of its own")
        self.assertEqual(LaborAllocation.objects.count(), 0)
        self.allocate_post(project=self.nowhere.pk, location=self.site2.pk)
        self.assertEqual(LaborAllocation.objects.count(), 1)

    def test_move_shows_history_on_the_worker_page(self):
        self.allocate_post()
        self.allocate_post(project=self.p2.pk, effective_from="2026-04-01")
        detail = self.client.get(self.url("labor_detail", self.p.pk))
        self.assertContains(detail, "Villa B")
        self.assertContains(detail, "31 Mar 2026")                    # the first stint ends the day before
        self.assertContains(self.client.get(self.url("labor_allocate", self.p.pk)), "Now at")

    def test_rule_errors_show_on_the_form(self):
        self.allocate_post()
        self.assertContains(self.allocate_post(), "already allocated there")                  # same place again
        self.assertContains(self.allocate_post(project=self.p2.pk), "must start after")       # a move on the same day
        self.assertContains(self.allocate_post(project=self.p2.pk, effective_from="2025-01-01"), "joined on")
        self.assertEqual(LaborAllocation.objects.count(), 1)

    def test_form_only_offers_active_projects_of_the_workers_company_sites_and_open_work_orders(self):
        self.p2.status = "closed"
        self.p2.save()
        open_wo = WorkOrder.objects.create(project=self.p1, code="WO-1", name="A")
        closed_wo = WorkOrder.objects.create(project=self.p1, code="WO-2", name="B", status="closed")
        foreign_wo = WorkOrder.objects.create(project=self.foreign, code="WO-3", name="C")
        form = self.client.get(self.url("labor_allocate", self.p.pk)).context["form"]
        self.assertEqual(set(form.fields["project"].queryset), {self.p1, self.nowhere})
        self.assertEqual(set(form.fields["location"].queryset), {self.site1, self.site2})
        self.assertEqual(set(form.fields["work_order"].queryset), {open_wo})
        self.assertNotIn(closed_wo, form.fields["work_order"].queryset)
        self.assertNotIn(foreign_wo, form.fields["work_order"].queryset)

    def test_work_order_must_belong_to_the_chosen_project(self):
        wo = WorkOrder.objects.create(project=self.p2, code="WO-9", name="Roof")
        r = self.allocate_post(work_order=wo.pk)
        self.assertContains(r, "belongs to another project")
        self.assertEqual(LaborAllocation.objects.count(), 0)

    def test_release(self):
        self.place()
        self.assertContains(self.client.get(self.url("labor_release", self.p.pk)), "Release")
        r = self.client.post(self.url("labor_release", self.p.pk), {"last_day": "2026-03-20"})
        self.assertRedirects(r, self.url("labor_detail", self.p.pk))
        self.assertIsNone(deployment.open_allocation(self.p.employee))
        self.assertContains(self.client.get(self.url("labor_detail", self.p.pk)), "Not on any site")
        again = self.client.get(self.url("labor_release", self.p.pk), follow=True)
        self.assertContains(again, "not allocated anywhere")
        bad = self.client.post(self.url("labor_release", self.p.pk), {"last_day": "2026-03-01"}, follow=True)
        self.assertContains(bad, "not allocated anywhere")

    def test_release_date_before_the_start_is_refused(self):
        self.place(on=date(2026, 3, 5))
        r = self.client.post(self.url("labor_release", self.p.pk), {"last_day": "2026-03-01"})
        self.assertContains(r, "cannot be before")

    def test_buttons_hidden_for_workers_who_left(self):
        self.place()
        Employee.objects.filter(pk=self.p.employee_id).update(status="separated")
        page = self.client.get(self.url("labor_detail", self.p.pk))
        self.assertNotContains(page, self.url("labor_allocate", self.p.pk))
        self.assertNotContains(page, self.url("labor_release", self.p.pk))


class ListAndSitePageTests(SitePageCase):
    def test_list_filters_by_project_and_by_not_on_any_site(self):
        other = self.worker("Imran")
        self.place()
        content = lambda **q: self.client.get(self.url("labor_list"), q).content.decode()
        self.assertIn("Ravi", content(project=self.p1.pk))
        self.assertNotIn("Imran", content(project=self.p1.pk))
        self.assertIn("Imran", content(project="none"))
        self.assertNotIn("Ravi", content(project="none"))
        self.assertIn("Ravi", content())

    def test_list_shows_tick_boxes_and_the_move_button_to_people_who_can_allocate(self):
        page = self.client.get(self.url("labor_list")).content.decode()
        self.assertIn(f'name="profiles" value="{self.p.pk}"', page)
        self.assertIn("Allocate / move ticked workers", page)

    def test_site_overview_and_detail(self):
        b = self.worker("Imran", engagement="contracted", contractor=self.contractor)
        self.place()
        self.place(profile=b)
        page = self.client.get(self.url("labor_site_list"))
        self.assertContains(page, "Tower A")
        row = next(r for r in page.context["rows"] if r["project"] == self.p1)
        self.assertEqual((row["total"], row["direct"], row["contracted"]), (2, 1, 1))
        detail = self.client.get(self.url("labor_site_detail", self.p1.pk, self.site1.pk))
        self.assertContains(detail, "Ravi")
        self.assertContains(detail, "Imran")
        self.assertContains(detail, "Gulf Manpower")

    def test_overview_links_to_workers_not_on_any_site(self):
        page = self.client.get(self.url("labor_site_list"))
        self.assertContains(page, "1 worker not on any site")
        self.assertContains(page, "?project=none")


class TransferPageTests(SitePageCase):
    def setUp(self):
        super().setUp()
        self.b = self.worker("Imran")
        self.c = self.worker("Salim")

    def post(self, profiles, follow=False, **over):
        data = {"profiles": [x.pk for x in profiles], "project": self.p2.pk, "location": "", "work_order": "",
                "effective_from": "2026-03-10"}
        data.update(over)
        return self.client.post(self.url("labor_transfer"), data, follow=follow)

    def test_ticked_workers_arrive_on_the_move_page(self):
        r = self.client.get(self.url("labor_transfer"), {"profiles": [self.p.pk, self.b.pk]})
        self.assertContains(r, "2 workers")
        self.assertContains(r, "Ravi")
        self.assertContains(r, "Imran")
        self.assertNotContains(r, "Salim")
        self.assertContains(r, f'value="{self.p.pk}"')                    # carried through as hidden fields

    def test_nothing_ticked_goes_back_to_the_list_with_a_message(self):
        r = self.client.get(self.url("labor_transfer"), follow=True)
        self.assertContains(r, "Tick at least one worker")

    def test_moves_the_crew_and_lands_on_the_new_site(self):
        self.place(self.p)
        r = self.post([self.p, self.b, self.c])
        self.assertRedirects(r, self.url("labor_site_detail", self.p2.pk, self.site2.pk))
        for profile in (self.p, self.b, self.c):
            self.assertEqual(deployment.open_allocation(profile.employee).project, self.p2)
        self.assertContains(self.client.get(self.url("labor_site_detail", self.p2.pk, self.site2.pk)), "Salim")

    def test_message_says_how_many_moved_and_how_many_were_already_there(self):
        self.place(self.p, project=self.p2, location=self.site2)
        r = self.post([self.p, self.b], follow=True)
        self.assertContains(r, "1 worker moved")
        self.assertContains(r, "1 was already there")

    def test_all_or_nothing_with_the_reasons_on_screen(self):
        Employee.objects.filter(pk=self.c.employee_id).update(status="separated")
        r = self.post([self.p, self.b, self.c])
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Nobody was moved")
        self.assertContains(r, "Salim")
        self.assertEqual(LaborAllocation.objects.count(), 0)

    def test_workers_from_another_company_cannot_be_moved_by_id(self):
        theirs = self.worker("Theirs", company=self.other_co)
        r = self.post([self.p, theirs])
        self.assertEqual(r.status_code, 200)                                  # the form is refused outright
        self.assertEqual(LaborAllocation.objects.count(), 0)

    def test_move_a_crew_to_a_work_order(self):
        wo = WorkOrder.objects.create(project=self.p2, code="WO-7", name="Roof")
        self.post([self.p, self.b], work_order=wo.pk)
        self.assertEqual(deployment.open_allocation(self.b.employee).work_order, wo)


class WorkOrderPageTests(SitePageCase):
    def test_add_edit_close_and_reopen(self):
        r = self.client.post(self.url("labor_work_order_create"), {
            "project": self.p1.pk, "code": " wo-1 ", "name": "Foundations", "start_date": "2026-03-01"})
        self.assertRedirects(r, self.url("labor_work_order_list"))
        wo = WorkOrder.objects.get()
        self.assertEqual((wo.code, wo.company_id), ("WO-1", self.co.pk))
        self.client.post(self.url("labor_work_order_edit", wo.pk), {
            "project": self.p2.pk, "code": "WO-1", "name": "Foundations and slab"})
        wo.refresh_from_db()
        self.assertEqual((wo.name, wo.project), ("Foundations and slab", self.p1))          # the project cannot move
        self.client.post(self.url("labor_work_order_toggle", wo.pk))
        wo.refresh_from_db()
        self.assertEqual(wo.status, "closed")
        self.client.post(self.url("labor_work_order_toggle", wo.pk))
        wo.refresh_from_db()
        self.assertEqual(wo.status, "open")

    def test_duplicate_number_in_a_project_and_bad_dates_are_refused(self):
        WorkOrder.objects.create(project=self.p1, code="WO-1", name="A")
        r = self.client.post(self.url("labor_work_order_create"), {"project": self.p1.pk, "code": "wo-1", "name": "B"})
        self.assertContains(r, "already has a work order with this number")
        r = self.client.post(self.url("labor_work_order_create"), {
            "project": self.p1.pk, "code": "WO-2", "name": "B", "start_date": "2026-05-01", "end_date": "2026-04-01"})
        self.assertContains(r, "cannot be before")
        self.assertEqual(WorkOrder.objects.count(), 1)

    def test_closing_is_blocked_while_workers_are_on_it(self):
        wo = WorkOrder.objects.create(project=self.p1, code="WO-1", name="A")
        self.place(work_order=wo)
        r = self.client.post(self.url("labor_work_order_toggle", wo.pk), follow=True)
        self.assertContains(r, "still allocated to WO-1")
        wo.refresh_from_db()
        self.assertEqual(wo.status, "open")

    def test_list_counts_workers_and_toggle_needs_post(self):
        wo = WorkOrder.objects.create(project=self.p1, code="WO-1", name="A")
        self.place(work_order=wo)
        page = self.client.get(self.url("labor_work_order_list"))
        self.assertEqual(page.context["orders"][0].worker_count, 1)
        self.assertEqual(self.client.get(self.url("labor_work_order_toggle", wo.pk)).status_code, 405)


class LayoutTests(SitePageCase):
    def test_every_form_places_every_field_it_has(self):
        wo = WorkOrder.objects.create(project=self.p1, code="WO-1", name="A")
        self.place()
        pages = [self.url("labor_allocate", self.p.pk), self.url("labor_release", self.p.pk),
                 self.url("labor_transfer") + f"?profiles={self.p.pk}", self.url("labor_work_order_create"),
                 self.url("labor_work_order_edit", wo.pk)]
        for url in pages:
            r = self.client.get(url)
            self.assertEqual(r.status_code, 200, url)
            placed = {n for section in r.context["layout"] for row in section["rows"] for n in row}
            self.assertEqual(placed, set(r.context["form"].fields), url)
