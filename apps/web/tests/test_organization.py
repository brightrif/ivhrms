from django.contrib.auth.models import Permission
from django.urls import reverse

from apps.accounts.models import User
from apps.attendance.tests import BaseCase
from apps.audit.models import AuditEvent
from apps.employees.models import Employee
from apps.organization.models import (Company, CompanyAccess, Department, Designation,
                                      Grade, Location, Project)


class DepartmentPageTests(BaseCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.hr_user)
        self.url = reverse("web:department_create")

    def test_staff_without_permission_are_blocked(self):
        self.client.force_login(self.emp_user)
        self.assertEqual(self.client.get(reverse("web:department_list")).status_code, 403)

    def test_create_generates_a_code_and_is_audited(self):
        self.assertRedirects(self.client.post(self.url, {"company": self.co.pk, "name": "Human Resources"}),
                             reverse("web:department_list"))
        d = Department.objects.get(name="Human Resources")
        self.assertEqual((d.company, d.code), (self.co, "HR"))
        self.client.post(self.url, {"company": self.co.pk, "name": "Health Reports"})
        self.assertEqual(Department.objects.get(name="Health Reports").code, "HR2")
        self.assertTrue(AuditEvent.objects.filter(module="organization", action="create",
                                                  object_id=str(d.pk)).exists())

    def test_duplicate_names_are_rejected_per_company_only(self):
        Department.objects.create(company=self.co, code="OPS", name="Operations")
        self.assertEqual(self.client.post(self.url, {"company": self.co.pk, "name": "operations"}).status_code, 200)
        self.assertEqual(Department.objects.filter(name__iexact="operations").count(), 1)
        other = Company.objects.create(code="C2", name="Company Two")
        CompanyAccess.objects.create(user=self.hr_user, company=other)
        self.assertRedirects(self.client.post(self.url, {"company": other.pk, "name": "Operations"}),
                             reverse("web:department_list"))

    def test_cannot_add_to_a_company_without_access(self):
        stranger = Company.objects.create(code="X9", name="Hidden Co")
        self.assertEqual(self.client.post(self.url, {"company": stranger.pk, "name": "Ops"}).status_code, 200)
        self.assertFalse(Department.objects.filter(company=stranger).exists())

    def test_edit_keeps_the_code_and_prevents_cycles(self):
        a = Department.objects.create(company=self.co, code="A", name="Alpha")
        b = Department.objects.create(company=self.co, code="B", name="Beta", parent=a)
        edit = reverse("web:department_edit", args=[a.pk])
        r = self.client.post(edit, {"name": "Alpha", "parent": b.pk})        # its own child
        self.assertEqual(r.status_code, 200)
        self.client.post(edit, {"company": 999, "code": "HACK", "name": "Alpha Renamed"})
        a.refresh_from_db()
        self.assertEqual((a.code, a.name, a.company), ("A", "Alpha Renamed", self.co))

    def test_deactivated_department_leaves_the_staff_form(self):
        d = Department.objects.create(company=self.co, code="OPS", name="Operations")
        fields = reverse("web:employee_company_fields")
        self.assertContains(self.client.get(fields, {"company": self.co.pk}), "Operations")
        self.client.post(reverse("web:department_toggle", args=[d.pk]))
        self.assertNotContains(self.client.get(fields, {"company": self.co.pk}), "Operations")

    def test_delete_is_blocked_while_staff_use_it(self):
        used = Department.objects.create(company=self.co, code="OPS", name="Operations")
        free = Department.objects.create(company=self.co, code="TMP", name="Temp")
        Employee.objects.filter(pk=self.emp.pk).update(department=used)
        self.client.force_login(User.objects.create_superuser("root", password="x"))
        self.assertContains(self.client.get(reverse("web:department_delete", args=[used.pk])), "cannot be deleted")
        self.client.post(reverse("web:department_delete", args=[used.pk]))
        self.assertTrue(Department.objects.filter(pk=used.pk).exists())
        self.assertRedirects(self.client.post(reverse("web:department_delete", args=[free.pk])),
                             reverse("web:department_list"))
        self.assertFalse(Department.objects.filter(pk=free.pk).exists())


class DesignationPageTests(BaseCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.hr_user)

    def test_create_duplicate_toggle_and_delete(self):
        url = reverse("web:designation_create")
        self.assertRedirects(self.client.post(url, {"name": "  Site   Engineer "}), reverse("web:designation_list"))
        d = Designation.objects.get()
        self.assertEqual(d.name, "Site Engineer")
        self.assertEqual(self.client.post(url, {"name": "site engineer"}).status_code, 200)
        self.assertEqual(Designation.objects.count(), 1)

        form_page = reverse("web:employee_create")
        option = ">Site Engineer</option>"          # the dropdown entry; flash messages also contain the name
        self.assertContains(self.client.get(form_page), option)
        self.client.post(reverse("web:designation_toggle", args=[d.pk]))
        self.assertNotContains(self.client.get(form_page), option)

        Employee.objects.filter(pk=self.emp.pk).update(designation=d)
        self.client.force_login(User.objects.create_superuser("root", password="x"))
        delete = reverse("web:designation_delete", args=[d.pk])
        self.assertContains(self.client.get(delete), "cannot be deleted")
        Employee.objects.filter(pk=self.emp.pk).update(designation=None)
        self.assertRedirects(self.client.post(delete), reverse("web:designation_list"))
        self.assertFalse(Designation.objects.exists())


def grant(user, *codenames):
    perms = Permission.objects.filter(content_type__app_label="organization", codename__in=codenames)
    user.user_permissions.add(*perms)


class OrgCase(BaseCase):
    def setUp(self):
        super().setUp()
        self.root = User.objects.create_superuser("orgroot", password="x")
        self.client.force_login(self.root)
        self.enable(self.co)                                   # the base company runs projects

    def enable(self, company, on=True):
        Company.objects.filter(pk=company.pk).update(runs_projects=on)

    def limited(self, *codenames, company=None):
        """A user who may work in one company and holds only the named organization permissions."""
        u = User.objects.create_user("orglimited")
        CompanyAccess.objects.create(user=u, company=company or self.co)
        grant(u, *codenames)
        return u


class OrganizationHomeTests(OrgCase):
    def test_a_user_with_no_organization_permission_is_refused(self):
        self.client.force_login(self.emp_user)
        self.assertEqual(self.client.get(reverse("web:organization_home")).status_code, 403)

    def test_lands_on_the_first_tab_the_user_may_see(self):
        home = reverse("web:organization_home")
        self.assertRedirects(self.client.get(home), reverse("web:company_list"), fetch_redirect_response=False)
        self.client.force_login(self.limited("view_department"))
        self.assertRedirects(self.client.get(home), reverse("web:department_list"), fetch_redirect_response=False)

    def test_tabs_follow_permissions(self):
        self.client.force_login(self.limited("view_department"))
        page = self.client.get(reverse("web:department_list"))
        self.assertContains(page, reverse("web:department_list"))
        for name in ("company_list", "project_list", "site_list"):
            self.assertNotContains(page, reverse(f"web:{name}"))
        self.client.force_login(self.root)
        page = self.client.get(reverse("web:department_list"))
        for name in ("company_list", "designation_list", "project_list", "site_list"):
            self.assertContains(page, reverse(f"web:{name}"))


class ProjectPageTests(OrgCase):
    def setUp(self):
        super().setUp()
        self.site = Location.objects.create(code="CAMP", name="Alpha Camp", is_site=True)
        self.url = reverse("web:project_create")

    def post_new(self, **over):
        data = {"company": self.co.pk, "code": "p-101", "name": "Hamad Tower",
                "location": self.site.pk, "status": "active"}
        data.update(over)
        return self.client.post(self.url, data)

    def test_create_uppercases_the_code(self):
        self.assertRedirects(self.post_new(), reverse("web:project_list"))
        p = Project.objects.get(name="Hamad Tower")
        self.assertEqual((p.code, p.company, p.location), ("P-101", self.co, self.site))

    def test_duplicate_code_is_rejected_per_company_only(self):
        self.post_new()
        self.assertEqual(self.post_new(name="Other").status_code, 200)
        other = Company.objects.create(code="C2", name="Company Two")
        self.enable(other)
        self.assertRedirects(self.post_new(company=other.pk, name="Other"), reverse("web:project_list"))
        self.assertEqual(Project.objects.filter(code="P-101").count(), 2)

    def test_end_date_cannot_precede_start_date(self):
        r = self.post_new(start_date="2026-10-10", end_date="2026-10-01")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Project.objects.filter(name="Hamad Tower").exists())

    def test_edit_keeps_company_and_code(self):
        p = Project.objects.create(company=self.co, code="P1", name="One")
        self.client.post(reverse("web:project_edit", args=[p.pk]),
                         {"company": 999, "code": "HACK", "name": "One Renamed", "status": "on_hold"})
        p.refresh_from_db()
        self.assertEqual((p.code, p.name, p.status, p.company), ("P1", "One Renamed", "on_hold", self.co))

    def test_close_and_reopen(self):
        p = Project.objects.create(company=self.co, code="P1", name="One")
        url = reverse("web:project_toggle", args=[p.pk])
        self.client.post(url)
        p.refresh_from_db()
        self.assertEqual(p.status, "closed")
        self.client.post(url)
        p.refresh_from_db()
        self.assertEqual(p.status, "active")

    def test_a_project_in_a_company_the_user_cannot_access_is_a_404(self):
        hidden = Company.objects.create(code="X9", name="Hidden Co")
        p = Project.objects.create(company=hidden, code="H1", name="Hidden Plaza")
        self.client.force_login(self.limited("view_project", "change_project"))
        self.assertEqual(self.client.get(reverse("web:project_edit", args=[p.pk])).status_code, 404)
        self.assertNotContains(self.client.get(reverse("web:project_list")), "Hidden Plaza")

    def test_list_filters_by_company(self):
        other = Company.objects.create(code="C2", name="Company Two")
        Project.objects.create(company=self.co, code="A1", name="Alpha Tower")
        Project.objects.create(company=other, code="B1", name="Beta Plaza")
        page = self.client.get(reverse("web:project_list"), {"company": other.pk})
        self.assertContains(page, "Beta Plaza")
        self.assertNotContains(page, "Alpha Tower")

    def test_a_free_project_can_be_deleted(self):
        p = Project.objects.create(company=self.co, code="P1", name="One")
        self.assertRedirects(self.client.post(reverse("web:project_delete", args=[p.pk])),
                             reverse("web:project_list"))
        self.assertFalse(Project.objects.filter(pk=p.pk).exists())

    # ---- which companies run projects

    def test_only_companies_that_run_projects_are_offered(self):
        other = Company.objects.create(code="C2", name="Company Two")
        field = lambda: self.client.get(self.url).context["form"].fields["company"]
        self.assertEqual(list(field().queryset), [self.co])
        self.assertTrue(field().widget.is_hidden)                 # one company: the form does not ask
        self.enable(other)
        self.assertEqual(set(field().queryset), {self.co, other})
        self.assertFalse(field().widget.is_hidden)

    def test_a_company_that_does_not_run_projects_is_refused(self):
        other = Company.objects.create(code="C2", name="Company Two")
        self.assertEqual(self.post_new(company=other.pk).status_code, 200)
        self.assertFalse(Project.objects.filter(name="Hamad Tower").exists())

    def test_with_no_company_running_projects_the_page_explains(self):
        self.enable(self.co, on=False)
        page = self.client.get(self.url)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "No company is set to run projects")
        self.assertContains(page, reverse("web:settings_module", args=["organization"]))

    def test_projects_of_a_switched_off_company_stay_visible_and_editable(self):
        other = Company.objects.create(code="C2", name="Company Two")         # never switched on
        p = Project.objects.create(company=other, code="B1", name="Beta Plaza")
        self.assertContains(self.client.get(reverse("web:project_list")), "Beta Plaza")
        self.assertEqual(self.client.get(reverse("web:project_edit", args=[p.pk])).status_code, 200)

    def test_the_settings_tab_lists_every_company_with_a_switch(self):
        other = Company.objects.create(code="C2", name="Company Two")
        page = self.client.get(reverse("web:settings_module", args=["organization"]))
        self.assertContains(page, "Company Two")
        for c in (self.co, other):
            self.assertContains(page, reverse("web:company_projects_set", args=[c.pk]))

    def test_the_switch_turns_projects_on_and_off_for_one_company(self):
        other = Company.objects.create(code="C2", name="Company Two")
        url = reverse("web:company_projects_set", args=[other.pk])
        offered = lambda: set(self.client.get(self.url).context["form"].fields["company"].queryset)
        self.assertEqual(offered(), {self.co})
        self.client.post(url, {f"runs-{other.pk}": "on"})
        other.refresh_from_db()
        self.assertTrue(other.runs_projects)
        self.assertEqual(offered(), {self.co, other})
        self.client.post(url, {})                                        # an unticked switch sends nothing
        other.refresh_from_db()
        self.assertFalse(other.runs_projects)
        self.client.post(url, {f"runs-{self.co.pk}": "on"})              # another company's switch is ignored
        other.refresh_from_db()
        self.assertFalse(other.runs_projects)

    def test_only_people_with_the_settings_right_may_use_the_switch(self):
        other = Company.objects.create(code="C2", name="Company Two")
        self.client.force_login(self.limited("view_project"))
        r = self.client.post(reverse("web:company_projects_set", args=[other.pk]), {f"runs-{other.pk}": "on"})
        self.assertEqual(r.status_code, 403)
        other.refresh_from_db()
        self.assertFalse(other.runs_projects)

    # ---- the Add project screen: name, then site (found by typing), then an automatic code

    def test_the_form_asks_for_the_name_then_the_site_then_the_code(self):
        page = self.client.get(self.url)
        html = page.content.decode()
        self.assertLess(html.index('name="name"'), html.index('name="location"'))
        self.assertLess(html.index('name="location"'), html.index('name="code"'))
        self.assertContains(page, "data-typeahead")

    def test_code_is_built_from_the_name_and_the_site(self):
        self.post_new(code="", name="Hamad Tower")
        self.post_new(code="", name="Hamad Tunnel")                      # same letters again: a number is added
        codes = sorted(Project.objects.filter(name__startswith="Hamad T").values_list("code", flat=True))
        self.assertEqual(codes, ["HAT-CAMP", "HAT-CAMP2"])

    def test_the_code_is_per_company_and_works_without_a_site(self):
        other = Company.objects.create(code="C2", name="Company Two")
        self.enable(other)
        self.post_new(code="", name="Hamad Tower")
        self.post_new(code="", name="Hamad Tower", company=other.pk)     # not taken in the other company
        self.assertTrue(Project.objects.filter(company=other, code="HAT-CAMP").exists())
        self.post_new(code="", name="Riffa", location="")
        riffa = Project.objects.get(name="Riffa")
        self.assertEqual(riffa.code, "RIF")
        self.assertIsNone(riffa.location)

    def test_typing_a_site_without_choosing_one_is_an_error(self):
        r = self.post_new(code="", location="", location_text="Alp")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Choose a site from the list")
        self.assertContains(r, 'value="Alp"')                            # what was typed is kept
        self.assertFalse(Project.objects.filter(name="Hamad Tower").exists())

    def test_the_code_preview_follows_the_name_and_the_site(self):
        url = reverse("web:project_code_preview")
        read = lambda **q: self.client.get(url, q).content.decode()
        self.assertEqual(read(name="Hamad Tower", location=self.site.pk, company=self.co.pk), "HAT-CAMP")
        self.assertEqual(read(name="Hamad Tower", company=self.co.pk), "HAT")
        Project.objects.create(company=self.co, code="HAT-CAMP", name="Existing")
        self.assertEqual(read(name="Hamad Tower", location=self.site.pk, company=self.co.pk), "HAT-CAMP2")
        self.assertEqual(read(location=self.site.pk), "")

    def test_site_search_suggests_matching_active_sites_best_first(self):
        Location.objects.create(code="RIF", name="Riffa Camp", is_site=True)
        Location.objects.create(code="ARI", name="Al Riffa Depot", is_site=True)
        Location.objects.create(code="OFF", name="Riffa Office", is_site=False)
        Location.objects.create(code="OLD", name="Riffa Old", is_site=True, is_active=False)
        url = reverse("web:site_search")
        names = lambda q: [r["name"] for r in self.client.get(url, {"q": q}).json()["results"]]
        self.assertEqual(names("ri"), ["Riffa Camp", "Al Riffa Depot"])  # starts-with first; offices and inactive sites left out
        self.assertEqual(names("zzz"), [])
        self.assertEqual(names(""), [])

    def test_site_search_needs_the_project_right(self):
        self.client.force_login(self.limited("view_location"))
        self.assertEqual(self.client.get(reverse("web:site_search"), {"q": "a"}).status_code, 403)

    def test_edit_shows_the_current_site_and_has_no_code_autofill(self):
        p = Project.objects.create(company=self.co, code="P1", name="One", location=self.site)
        page = self.client.get(reverse("web:project_edit", args=[p.pk]))
        self.assertContains(page, 'value="Alpha Camp"')
        self.assertNotContains(page, 'data-autofill-from="')


class SitePageTests(OrgCase):
    def setUp(self):
        super().setUp()
        self.url = reverse("web:site_create")

    def test_create_generates_a_code(self):
        self.assertRedirects(self.client.post(self.url, {"name": "Hamad Camp", "is_site": "on"}),
                             reverse("web:site_list"))
        self.assertEqual(Location.objects.get(name="Hamad Camp").code, "HAC")
        self.client.post(self.url, {"name": "Hamad City", "is_site": "on"})
        self.assertEqual(Location.objects.get(name="Hamad City").code, "HAC2")

    def test_code_is_three_letters_from_the_name(self):
        for name, code in [("Riffa", "RIF"), ("Al Hamad Camp", "AHC"), ("Head Office", "HEO")]:
            self.client.post(self.url, {"name": name, "is_site": "on"})
            self.assertEqual(Location.objects.get(name=name).code, code)

    def test_a_typed_code_is_kept(self):
        self.client.post(self.url, {"name": "Head Office", "code": "hq1"})
        self.assertEqual(Location.objects.get(name="Head Office").code, "HQ1")

    def test_the_form_uses_the_staff_page_layout(self):
        page = self.client.get(self.url)
        self.assertContains(page, "form-compact")
        self.assertContains(page, "form-actions")

    def test_duplicate_names_are_rejected(self):
        Location.objects.create(code="HO", name="Head Office")
        self.assertEqual(self.client.post(self.url, {"name": "head office"}).status_code, 200)
        self.assertEqual(Location.objects.filter(name__iexact="head office").count(), 1)

    def test_the_list_separates_sites_from_offices(self):
        Location.objects.create(code="A", name="Alpha Camp", is_site=True)
        Location.objects.create(code="B", name="Beta Office", is_site=False)
        url = reverse("web:site_list")
        page = self.client.get(url)
        self.assertContains(page, "Alpha Camp")
        self.assertNotContains(page, "Beta Office")
        page = self.client.get(url, {"kind": "offices"})
        self.assertContains(page, "Beta Office")
        self.assertNotContains(page, "Alpha Camp")
        page = self.client.get(url, {"kind": "all"})
        self.assertContains(page, "Alpha Camp")
        self.assertContains(page, "Beta Office")

    def test_a_deactivated_site_leaves_the_project_site_search(self):
        site = Location.objects.create(code="A", name="Alpha Camp", is_site=True)
        search = lambda: [r["name"] for r in self.client.get(reverse("web:site_search"), {"q": "alp"}).json()["results"]]
        self.assertEqual(search(), ["Alpha Camp"])
        self.client.post(reverse("web:site_toggle", args=[site.pk]), follow=True)   # follow: shows and clears the flash message
        site.refresh_from_db()
        self.assertFalse(site.is_active)
        self.assertEqual(search(), [])

    def test_a_site_used_by_a_project_cannot_be_deleted(self):
        site = Location.objects.create(code="A", name="Alpha Camp", is_site=True)
        Project.objects.create(company=self.co, code="P1", name="One", location=site)
        r = self.client.post(reverse("web:site_delete", args=[site.pk]))
        self.assertEqual(r.status_code, 200)                       # the blockers page, not a redirect
        self.assertTrue(Location.objects.filter(pk=site.pk).exists())

    def test_the_code_preview_shows_what_will_be_saved(self):
        url = reverse("web:site_code_preview")
        read = lambda **q: self.client.get(url, q).content.decode()
        self.assertEqual(read(name="Hamad Camp"), "HAC")
        Location.objects.create(code="HAC", name="Another", is_site=True)
        self.assertEqual(read(name="Hamad Camp"), "HAC2")        # the number is added, as on save
        self.assertEqual(read(name="  "), "")
        self.assertEqual(read(), "")

    def test_the_code_box_fills_itself_on_the_add_form_only(self):
        hook = 'data-autofill-from="id_name"'
        self.assertContains(self.client.get(self.url), hook)
        site = Location.objects.create(code="ALP", name="Alpha Camp", is_site=True)
        self.assertNotContains(self.client.get(reverse("web:site_edit", args=[site.pk])), hook)


class GradePageTests(OrgCase):
    def test_create_generates_a_code(self):
        url = reverse("web:grade_create")
        self.assertRedirects(self.client.post(url, {"name": "Senior Engineer", "rank": 5}),
                             reverse("web:designation_list"))
        self.assertEqual(Grade.objects.get(name="Senior Engineer").code, "SE")
        self.client.post(url, {"name": "Site Engineer", "rank": 3})
        self.assertEqual(Grade.objects.get(name="Site Engineer").code, "SE2")

    def test_duplicate_names_are_rejected(self):
        Grade.objects.create(code="G1", name="Manager", rank=7)
        r = self.client.post(reverse("web:grade_create"), {"name": "manager", "rank": 7})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Grade.objects.filter(name__iexact="manager").count(), 1)

    def test_edit_keeps_the_code(self):
        g = Grade.objects.create(code="G1", name="Manager", rank=7)
        self.client.post(reverse("web:grade_edit", args=[g.pk]), {"name": "Senior Manager", "code": "HACK", "rank": 8})
        g.refresh_from_db()
        self.assertEqual((g.code, g.name, g.rank), ("G1", "Senior Manager", 8))

    def test_grades_show_beside_designations_only_with_the_grade_permission(self):
        Grade.objects.create(code="G1", name="Principal Grade", rank=9)
        self.assertContains(self.client.get(reverse("web:designation_list")), "Principal Grade")
        self.client.force_login(self.limited("view_designation"))
        self.assertNotContains(self.client.get(reverse("web:designation_list")), "Principal Grade")

    def test_delete(self):
        g = Grade.objects.create(code="G1", name="Manager", rank=7)
        self.assertRedirects(self.client.post(reverse("web:grade_delete", args=[g.pk])),
                             reverse("web:designation_list"))
        self.assertFalse(Grade.objects.filter(pk=g.pk).exists())