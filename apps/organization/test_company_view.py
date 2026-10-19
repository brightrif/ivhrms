from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase
from django.urls import reverse

from apps.audit.context import reset_request, set_request
from apps.organization import scope
from apps.organization.models import Company, CompanyAccess
from apps.organization.services import all_companies_for, companies_for

User = get_user_model()


class CompanyViewCase(TestCase):
    def setUp(self):
        self.spare = Company.objects.create(code="ISP", name="IV Spare Parts", is_default=True)
        self.build = Company.objects.create(code="IVC", name="IV Construction")
        self.both = self.person("both", self.spare, self.build)
        self.only_build = self.person("only_build", self.build)
        self.only_spare = self.person("only_spare", self.spare)
        self.root = self.person("root")
        self.root.is_superuser = True
        self.root.is_staff = True
        self.root.save()

    def person(self, name, *companies):
        user = User.objects.create_user(name)
        for company in companies:
            CompanyAccess.objects.create(user=user, company=company)
        return user

    def visit(self, user):
        """Sign in as `user`, open a page, and return the request the middleware worked on."""
        self.client.force_login(user)
        return self.client.get(reverse("web:dashboard")).wsgi_request


class CombinedViewTests(CompanyViewCase):
    def test_combined_is_the_default_and_changes_nothing(self):
        self.assertEqual(scope.current_view(), scope.COMBINED)
        request = self.visit(self.both)
        self.assertIsNone(request.work_company)
        self.assertFalse(request.company_view_separate)
        self.assertEqual(set(companies_for(self.both)), {self.spare, self.build})

    def test_a_manager_with_one_company_sees_only_that_company_in_either_view(self):
        self.assertEqual(list(companies_for(self.only_build)), [self.build])
        scope.set_view(scope.SEPARATE)
        self.assertEqual(list(companies_for(self.only_build)), [self.build])


class SeparateViewTests(CompanyViewCase):
    def setUp(self):
        super().setUp()
        scope.set_view(scope.SEPARATE)

    def test_a_login_with_two_companies_starts_on_the_default_and_can_switch(self):
        request = self.visit(self.both)
        self.assertEqual(request.work_company, self.spare)
        self.assertEqual(request.work_companies, [self.spare, self.build])
        self.client.post(reverse("web:company_switch"), {"company": self.build.pk})
        self.assertEqual(self.client.get(reverse("web:dashboard")).wsgi_request.work_company, self.build)
        self.assertEqual(self.client.get(reverse("web:dashboard")).wsgi_request.work_company, self.build)

    def test_a_login_with_one_company_works_in_it_and_cannot_switch_to_the_other(self):
        request = self.visit(self.only_build)
        self.assertEqual((request.work_company, request.work_companies), (self.build, [self.build]))
        r = self.client.post(reverse("web:company_switch"), {"company": self.spare.pk})
        self.assertEqual(r.status_code, 404)
        self.assertEqual(self.client.get(reverse("web:dashboard")).wsgi_request.work_company, self.build)

    def test_a_company_that_is_not_a_number_is_refused(self):
        self.client.force_login(self.both)
        self.assertEqual(self.client.post(reverse("web:company_switch"), {"company": "x"}).status_code, 404)

    def test_the_superuser_can_work_in_either_company_but_one_at_a_time(self):
        request = self.visit(self.root)
        self.assertEqual(request.work_company, self.spare)
        self.assertEqual(len(request.work_companies), 2)

    def test_companies_for_narrows_only_the_person_signed_in_on_the_request(self):
        request = RequestFactory().get("/")
        request.user = self.both
        request.company_scope = self.build.pk
        token = set_request(request)
        try:
            self.assertEqual(list(companies_for(self.both)), [self.build])
            self.assertEqual(set(all_companies_for(self.both)), {self.spare, self.build})
            self.assertEqual(list(companies_for(self.only_spare)), [self.spare])   # someone else's list is not narrowed
        finally:
            reset_request(token)

    def test_nothing_is_narrowed_outside_a_web_request(self):
        self.assertEqual(set(companies_for(self.both)), {self.spare, self.build})


class ChangingTheViewTests(CompanyViewCase):
    def test_only_someone_with_the_right_may_change_the_view(self):
        self.client.force_login(self.only_spare)
        r = self.client.post(reverse("web:company_view_set"), {"company_view": "separate"})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(scope.current_view(), scope.COMBINED)

    def test_the_superuser_changes_it_and_it_is_logged(self):
        from apps.audit.models import AuditEvent

        self.client.force_login(self.root)
        r = self.client.post(reverse("web:company_view_set"), {"company_view": "separate"},
                             HTTP_HX_REQUEST="true")
        self.assertEqual((r.status_code, r["HX-Refresh"]), (200, "true"))
        self.assertEqual(scope.current_view(), scope.SEPARATE)
        self.assertTrue(AuditEvent.objects.filter(module="organization", changes__company_view__new="separate").exists())

    def test_an_unknown_view_changes_nothing(self):
        self.client.force_login(self.root)
        self.client.post(reverse("web:company_view_set"), {"company_view": "weird"})
        self.assertEqual(scope.current_view(), scope.COMBINED)

    def test_the_organization_settings_tab_shows_the_section(self):
        self.client.force_login(self.root)
        page = self.client.get(reverse("web:settings_module", args=["organization"]))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Company view")
        self.assertContains(page, reverse("web:company_view_set"))


class ColourTests(CompanyViewCase):
    def test_colours_are_saved_checked_and_cleared(self):
        self.client.force_login(self.root)
        url = reverse("web:company_colours_set", args=[self.build.pk])
        self.client.post(url, {"theme_color": "#1A5FA8", "sidebar_color": "#102030"})
        self.build.refresh_from_db()
        self.assertEqual((self.build.theme_color, self.build.sidebar_color), ("#1a5fa8", "#102030"))
        self.client.post(url, {"theme_color": "red", "sidebar_color": "#000000"})
        self.build.refresh_from_db()
        self.assertEqual(self.build.theme_color, "#1a5fa8")                      # a bad colour saves nothing
        self.client.post(url, {"reset": "1", "theme_color": "#ffffff", "sidebar_color": "#ffffff"})
        self.build.refresh_from_db()
        self.assertEqual((self.build.theme_color, self.build.sidebar_color), ("", ""))

    def test_a_person_without_the_right_cannot_set_colours(self):
        self.client.force_login(self.only_build)
        r = self.client.post(reverse("web:company_colours_set", args=[self.build.pk]), {"theme_color": "#112233"})
        self.assertEqual(r.status_code, 403)

    def test_the_theme_is_built_from_the_company_and_empty_when_standard(self):
        self.assertIsNone(scope.build_theme(self.spare))
        self.build.theme_color, self.build.sidebar_color = "#1a5fa8", "#102030"
        theme = scope.build_theme(self.build)
        self.assertEqual((theme["primary"], theme["primary_rgb"], theme["sidebar"]), ("#1a5fa8", "26, 95, 168", "#102030"))
        self.assertEqual(theme["on_primary"], "#fff")

    def test_colours_show_on_the_page_in_separate_view_only(self):
        from django.template.loader import render_to_string

        self.build.theme_color = "#1a5fa8"
        self.build.save()
        self.client.force_login(self.only_build)

        def theme_html():
            request = self.client.get(reverse("web:dashboard")).wsgi_request
            return render_to_string("web/_company_theme.html", request=request)

        self.assertNotIn('id="company-theme"', theme_html())
        scope.set_view(scope.SEPARATE)
        self.assertIn('id="company-theme"', theme_html())
