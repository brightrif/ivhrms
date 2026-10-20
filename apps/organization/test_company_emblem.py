from django.contrib.auth import get_user_model
from django.template.loader import render_to_string
from django.test import TestCase
from django.urls import reverse

from apps.organization import scope
from apps.organization.models import Company, CompanyAccess

User = get_user_model()


class SwitchAndEmblemTests(TestCase):
    def setUp(self):
        self.spare = Company.objects.create(code="ISP", name="IV Spare Parts")                    # emblem left on Automatic
        self.build = Company.objects.create(code="IVC", name="IV Construction")
        self.root = User.objects.create_user("root")
        self.root.is_superuser = True
        self.root.is_staff = True
        self.root.save()
        self.both = self.person("both", self.spare, self.build)
        self.only = self.person("only", self.build)

    def person(self, name, *companies):
        user = User.objects.create_user(name)
        for company in companies:
            CompanyAccess.objects.create(user=user, company=company)
        return user

    def render(self, template, user):
        """The template as that person's page would draw it, in the Separate view."""
        scope.set_view(scope.SEPARATE)
        self.client.force_login(user)
        request = self.client.get(reverse("web:settings_home")).wsgi_request
        return render_to_string(template, request=request)

    # ---- the switch
    def test_two_companies_show_two_buttons_with_the_working_company_marked(self):
        html = self.render("web/_company_switcher.html", self.both)
        self.assertNotIn("<select", html)
        self.assertEqual(html.count('aria-current="true"'), 1)
        self.assertIn(f'name="company" value="{self.build.pk}"', html)           # the other company is the clickable one
        self.assertNotIn(f'name="company" value="{self.spare.pk}"', html)        # the one being worked in is not
        self.assertIn("IV Spare Parts", html)

    def test_the_other_button_is_clickable_after_switching(self):
        self.render("web/_company_switcher.html", self.both)
        self.client.post(reverse("web:company_switch"), {"company": self.build.pk})
        request = self.client.get(reverse("web:settings_home")).wsgi_request
        html = render_to_string("web/_company_switcher.html", request=request)
        self.assertIn(f'name="company" value="{self.spare.pk}"', html)
        self.assertNotIn(f'name="company" value="{self.build.pk}"', html)

    def test_one_company_shows_only_its_name(self):
        html = self.render("web/_company_switcher.html", self.only)
        self.assertNotIn("<button", html)
        self.assertNotIn("<select", html)
        self.assertIn("IV Construction", html)

    def test_three_companies_fall_back_to_a_dropdown(self):
        third = Company.objects.create(code="X", name="Third Co")
        CompanyAccess.objects.create(user=self.both, company=third)
        html = self.render("web/_company_switcher.html", self.both)
        self.assertIn("<select", html)
        self.assertNotIn("<button", html)

    def test_nothing_shows_in_the_combined_view(self):
        self.client.force_login(self.both)
        request = self.client.get(reverse("web:settings_home")).wsgi_request
        self.assertEqual(render_to_string("web/_company_switcher.html", request=request).strip(), "")
        self.assertEqual(render_to_string("web/_company_emblem.html", request=request).strip(), "")

    # ---- the emblem
    def test_each_company_shows_its_own_emblem(self):
        html = self.render("web/_company_emblem.html", self.both)             # starts on the company set up first: spare
        self.assertIn('<g class="ce-spin ce-gear-a">', html)                     # the drawn element, not the shared CSS
        self.assertNotIn('<g class="ce-trolley">', html)
        self.assertIn('<div class="ce-name">Spare Parts</div>', html)          # the brand above already says IV
        self.client.post(reverse("web:company_switch"), {"company": self.build.pk})
        request = self.client.get(reverse("web:settings_home")).wsgi_request
        html = render_to_string("web/_company_emblem.html", request=request)
        self.assertIn('<g class="ce-trolley">', html)
        self.assertNotIn('<g class="ce-spin ce-gear-a">', html)
        self.assertIn('<div class="ce-name">Construction</div>', html)

    def test_automatic_picks_the_emblem_from_the_company_name(self):
        from apps.organization.scope import emblem_for

        self.assertEqual(emblem_for(Company(name="IV Spare Parts")), "gears")
        self.assertEqual(emblem_for(Company(name="IV Construction")), "crane")
        self.assertEqual(emblem_for(Company(name="Gulf Trading")), "")

    def test_a_chosen_emblem_wins_over_the_name_and_none_switches_it_off(self):
        from apps.organization.scope import emblem_for

        self.assertEqual(emblem_for(Company(name="IV Construction", emblem="gears")), "gears")
        self.assertEqual(emblem_for(Company(name="Gulf Trading", emblem="crane")), "crane")
        self.assertEqual(emblem_for(Company(name="IV Construction", emblem="none")), "")
        self.build.emblem = "none"
        self.build.save()
        self.assertEqual(self.render("web/_company_emblem.html", self.only).strip(), "")

    def test_the_emblem_is_on_the_page_in_the_separate_view_only(self):
        self.client.force_login(self.root)
        url = reverse("web:settings_module", args=["organization"])
        self.assertNotContains(self.client.get(url), 'class="company-emblem"')
        scope.set_view(scope.SEPARATE)
        self.assertContains(self.client.get(url), 'class="company-emblem"')

    def test_the_emblem_is_chosen_on_the_settings_page_and_saved_with_the_colours(self):
        self.client.force_login(self.root)
        page = self.client.get(reverse("web:settings_module", args=["organization"]))
        for company in (self.spare, self.build):
            self.assertContains(page, f'name="emblem_{company.pk}"')
        url = reverse("web:company_colours_set", args=[self.build.pk])
        self.client.post(url, {"by_tick": "1", f"emblem_{self.build.pk}": "gears"})
        self.build.refresh_from_db()
        self.assertEqual(self.build.emblem, "gears")
        self.client.post(url, {"by_tick": "1", f"emblem_{self.build.pk}": "nonsense"})
        self.build.refresh_from_db()
        self.assertEqual(self.build.emblem, "gears")                            # a bad value saves nothing
        self.client.post(url, {"by_tick": "1", f"emblem_{self.build.pk}": "none"})
        self.build.refresh_from_db()
        self.assertEqual(self.build.emblem, "none")
        self.client.post(url, {"by_tick": "1", f"emblem_{self.build.pk}": ""})         # blank = Automatic
        self.build.refresh_from_db()
        self.assertEqual(self.build.emblem, "")
        self.client.post(url, {"by_tick": "1", f"emblem_{self.build.pk}": "crane"})
        self.client.post(url, {"by_tick": "1"})                                 # no emblem field: left as it was
        self.build.refresh_from_db()
        self.assertEqual(self.build.emblem, "crane")

    def test_the_browser_tab_names_the_company_in_the_separate_view_only(self):
        self.client.force_login(self.root)
        url = reverse("web:settings_module", args=["organization"])
        self.assertNotContains(self.client.get(url), "IV Spare Parts · Ivhrms")
        scope.set_view(scope.SEPARATE)
        self.assertContains(self.client.get(url), "settings · IV Spare Parts · Ivhrms</title>")
