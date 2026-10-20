from django.contrib.auth import get_user_model
from django.template.loader import render_to_string
from django.test import TestCase
from django.urls import reverse

from apps.organization import scope
from apps.organization.models import Company, CompanyAccess

User = get_user_model()


class CompanyViewScreenTests(TestCase):
    def setUp(self):
        self.first = Company.objects.create(code="ZZ", name="Zeta Trading")                # set up first
        self.second = Company.objects.create(code="AA", name="Alpha Works", is_default=True)
        self.root = User.objects.create_user("root")
        self.root.is_superuser = True
        self.root.is_staff = True
        self.root.save()
        self.both = User.objects.create_user("both")
        for company in (self.first, self.second):
            CompanyAccess.objects.create(user=self.both, company=company)

    def theme_html(self, company):
        return render_to_string("web/_company_theme.html", {"company_theme": scope.build_theme(company)})

    def test_a_login_with_two_companies_starts_on_the_company_set_up_first(self):
        scope.set_view(scope.SEPARATE)
        self.client.force_login(self.both)
        request = self.client.get(reverse("web:dashboard")).wsgi_request
        self.assertEqual(request.work_company, self.first)          # not the one flagged default, not first by name
        self.assertEqual(request.work_companies, [self.first, self.second])

    def test_the_default_company_settings_are_gone_from_the_settings_page(self):
        self.client.force_login(self.root)
        org = self.client.get(reverse("web:settings_module", args=["organization"]))
        self.assertNotContains(org, "Default companies")
        self.assertContains(org, "Company view")
        labor = self.client.get(reverse("web:settings_module", args=["labor"]))
        self.assertNotContains(labor, "Same as Add staff member")

    def test_only_the_ticked_colours_are_kept(self):
        self.client.force_login(self.root)
        url = reverse("web:company_colours_set", args=[self.first.pk])
        self.client.post(url, {"by_tick": "1", "theme_on": "1", "theme_color": "#1A5FA8", "sidebar_color": "#102030"})
        self.first.refresh_from_db()
        self.assertEqual((self.first.theme_color, self.first.sidebar_color), ("#1a5fa8", ""))
        self.client.post(url, {"by_tick": "1", "sidebar_on": "1", "theme_color": "#1A5FA8", "sidebar_color": "#102030"})
        self.first.refresh_from_db()
        self.assertEqual((self.first.theme_color, self.first.sidebar_color), ("", "#102030"))
        self.client.post(url, {"by_tick": "1", "theme_color": "#1A5FA8", "sidebar_color": "#102030"})
        self.first.refresh_from_db()
        self.assertEqual((self.first.theme_color, self.first.sidebar_color), ("", ""))

    def test_saving_one_row_ignores_the_other_rows_the_page_also_sends(self):
        """The Settings table is inside one form and HTMX posts all of it, so both companies' fields arrive together."""
        self.client.force_login(self.root)
        a, b = self.first.pk, self.second.pk
        everything = {"by_tick": "1",
                      f"theme_on_{a}": "1", f"theme_color_{a}": "#2f7d6b", f"sidebar_on_{a}": "1", f"sidebar_color_{a}": "#1f3a3a",
                      f"theme_color_{b}": "#31629c", f"sidebar_color_{b}": "#233460"}
        self.client.post(reverse("web:company_colours_set", args=[a]), everything)
        self.first.refresh_from_db()
        self.second.refresh_from_db()
        self.assertEqual((self.first.theme_color, self.first.sidebar_color), ("#2f7d6b", "#1f3a3a"))
        self.assertEqual((self.second.theme_color, self.second.sidebar_color), ("", ""))
        self.client.post(reverse("web:company_colours_set", args=[b]), everything)       # b's own ticks are off
        self.first.refresh_from_db()
        self.second.refresh_from_db()
        self.assertEqual((self.first.theme_color, self.first.sidebar_color), ("#2f7d6b", "#1f3a3a"))
        self.assertEqual((self.second.theme_color, self.second.sidebar_color), ("", ""))

    def test_the_settings_page_names_each_rows_fields_after_its_company(self):
        self.client.force_login(self.root)
        page = self.client.get(reverse("web:settings_module", args=["organization"]))
        for company in (self.first, self.second):
            for name in ("theme_on", "theme_color", "sidebar_on", "sidebar_color"):
                self.assertContains(page, f'name="{name}_{company.pk}"')

    def test_the_colours_replace_the_variables_the_stylesheet_uses(self):
        self.first.theme_color, self.first.sidebar_color = "#1a5fa8", "#102030"
        html = self.theme_html(self.first)
        for text in ("--iv-blue: #1a5fa8", "--bs-primary: #1a5fa8", "--bs-btn-bg: #1a5fa8",
                     "--sidebar-bg: #102030", "--iv-navy: #102030", ".topbar { border-top: 3px solid #1a5fa8"):
            self.assertIn(text, html)

    def test_a_company_with_only_a_sidebar_colour_keeps_its_standard_buttons(self):
        self.first.sidebar_color = "#102030"
        html = self.theme_html(self.first)
        self.assertIn("--sidebar-bg: #102030", html)
        self.assertNotIn("--bs-btn-bg", html)
        self.assertNotIn("--iv-blue", html)

    def test_no_colours_means_no_style_block(self):
        self.assertEqual(self.theme_html(self.second).strip(), "")

    def test_tints_are_lighter_and_shades_darker_than_the_colour(self):
        theme = scope.build_theme(Company(theme_color="#31629c"))
        total = lambda colour: sum(int(colour[i:i + 2], 16) for i in (1, 3, 5))
        self.assertGreater(total(theme["subtle"]), total("#31629c"))
        self.assertGreater(total(theme["accent"]), total("#31629c"))
        self.assertLess(total(theme["hover"]), total("#31629c"))
        self.assertLess(total(theme["emphasis"]), total("#31629c"))
