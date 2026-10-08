from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import TestCase
from django.urls import reverse

from apps.audit.models import AuditEvent
from apps.configuration import registry
from apps.configuration import services as svc
from apps.configuration.models import SettingValue
from apps.configuration.tests import TestModule
from apps.organization.models import Company, CompanyAccess

D = Decimal
User = get_user_model()


class SettingsPageCase(TestCase):
    def setUp(self):
        self.co = Company.objects.create(code="IV1", name="IV One")
        self.other = Company.objects.create(code="IV2", name="IV Two")
        self.root = User.objects.create_user("root", is_superuser=True)
        self.hr = self.user("hr", "HR")
        self.viewer = self.user("viewer", "Finance")
        self.nobody = User.objects.create_user("nobody")
        self.client.force_login(self.root)

    def user(self, name, group):
        u = User.objects.create_user(name)
        u.groups.add(Group.objects.get(name=group))
        CompanyAccess.objects.create(user=u, company=self.co)
        CompanyAccess.objects.create(user=u, company=self.other)
        return u

    def grant(self, user_or_group, *codenames):
        perms = Permission.objects.filter(content_type__app_label="configuration", codename__in=codenames)
        user_or_group.permissions.add(*perms) if isinstance(user_or_group, Group) else user_or_group.user_permissions.add(*perms)

    def url(self, name, *args):
        return reverse(f"web:{name}", args=args)

    def labor(self, **q):
        return self.client.get(self.url("settings_module", "labor"), q)

    def save_labor(self, **over):
        data = {"s__labor.default_standard_hours": "8", "s__labor.new_worker_overtime_eligible": "on",
                "s__labor.sheet_period": "week"}
        data.update(over)
        return self.client.post(self.url("settings_module", "labor"), data)


class AccessTests(SettingsPageCase):
    def test_needs_login(self):
        self.client.logout()
        for url in (self.url("settings_home"), self.url("settings_module", "labor"), self.url("settings_access")):
            self.assertEqual(self.client.get(url).status_code, 302, url)

    def test_nobody_gets_in_until_the_superuser_gives_the_right(self):
        self.client.force_login(self.hr)
        self.assertEqual(self.labor().status_code, 403)
        self.assertEqual(self.client.get(self.url("settings_home")).status_code, 403)
        self.assertEqual(self.save_labor().status_code, 403)
        self.assertEqual(SettingValue.objects.count(), 0)

    def test_view_right_shows_the_tab_but_not_the_ability_to_change(self):
        self.grant(self.viewer, "view_settings_labor")
        self.client.force_login(User.objects.get(pk=self.viewer.pk))
        page = self.labor()
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "You can see these settings but not change them")
        self.assertNotContains(page, "Save settings")
        self.assertContains(page, " disabled")
        self.assertEqual(self.save_labor(**{"s__labor.sheet_period": "month"}).status_code, 403)
        self.assertEqual(svc.get("labor.sheet_period"), "week")

    def test_edit_right_changes_settings(self):
        self.grant(self.hr, "edit_settings_labor")
        self.client.force_login(User.objects.get(pk=self.hr.pk))
        self.assertEqual(self.save_labor(**{"s__labor.sheet_period": "month"}).status_code, 302)
        self.assertEqual(svc.get("labor.sheet_period"), "month")

    def test_a_group_right_works_like_a_personal_one(self):
        self.grant(Group.objects.get(name="HR"), "edit_settings_labor")
        self.client.force_login(User.objects.get(pk=self.hr.pk))
        self.assertEqual(self.labor().status_code, 200)

    def test_the_access_tab_is_for_superusers_only(self):
        self.grant(self.hr, "edit_settings_labor")
        self.client.force_login(User.objects.get(pk=self.hr.pk))
        self.assertEqual(self.client.get(self.url("settings_access")).status_code, 403)
        self.assertEqual(self.client.post(self.url("settings_access"), {"grant": ["labor.add_overtimeclaim|1"]}).status_code, 403)
        self.assertNotContains(self.labor(), self.url("settings_access"))
        self.client.force_login(self.root)
        self.assertContains(self.labor(), self.url("settings_access"))

    def test_unknown_tab_is_a_404(self):
        self.assertEqual(self.client.get(self.url("settings_module", "nonsense")).status_code, 404)

    def test_home_goes_to_the_first_tab_or_access(self):
        self.assertRedirects(self.client.get(self.url("settings_home")), self.url("settings_module", "labor"),
                             fetch_redirect_response=False)


class ModulePageTests(SettingsPageCase):
    def test_shows_each_section_with_its_current_values(self):
        page = self.labor()
        self.assertEqual(page.status_code, 200)
        for text in ("Overtime", "Workers", "Timesheets", "Sheets", "Standard hours per day for new workers",
                     "Period the attendance and timesheet sheets open on"):
            self.assertContains(page, text)

    def test_saving_changes_and_reports_how_many(self):
        r = self.save_labor(**{"s__labor.default_standard_hours": "9", "s__labor.sheet_period": "month"})
        self.assertRedirects(r, self.url("settings_module", "labor"), fetch_redirect_response=False)
        self.assertEqual((svc.get("labor.default_standard_hours"), svc.get("labor.sheet_period")), (D("9"), "month"))
        self.assertContains(self.client.get(r.url), "Saved 2 changes")

    def test_untouched_form_stores_nothing_and_says_so(self):
        r = self.save_labor()
        self.assertEqual(SettingValue.objects.count(), 0)
        self.assertContains(self.client.get(r.url), "Nothing changed")

    def test_a_switch_that_is_left_out_is_turned_off(self):
        data = {"s__labor.default_standard_hours": "8", "s__labor.sheet_period": "week"}          # eligible box absent
        self.client.post(self.url("settings_module", "labor"), data)
        self.assertFalse(svc.get("labor.new_worker_overtime_eligible"))

    def test_bad_values_save_nothing_and_are_shown_where_they_are(self):
        r = self.save_labor(**{"s__labor.default_standard_hours": "30", "s__labor.sheet_period": "year"})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "must be at most 24")
        self.assertContains(r, "choose one of the listed options")
        self.assertContains(r, "Nothing was saved")
        self.assertContains(r, 'value="30"')                         # what was typed is kept
        self.assertEqual(SettingValue.objects.count(), 0)
        self.assertEqual(svc.get("labor.default_standard_hours"), D("8"))

    def test_changes_are_audited(self):
        self.save_labor(**{"s__labor.sheet_period": "day"})
        event = AuditEvent.objects.filter(module="configuration", action="create").get()
        self.assertEqual(event.changes["value"]["new"], "day")

    def test_the_overtime_section_lists_each_company_and_its_decision(self):
        from datetime import date
        from apps.labor import overtime_services as ot
        ot.set_policy(self.co, effective_from=date(2026, 1, 1), working_day_multiplier=D("1.25"),
                      weekly_off_multiplier=D("1.5"), holiday_multiplier=D("1.5"), all_hours_on_days_off=True,
                      monthly_divisor=30)
        ot.set_policy(self.other, effective_from=date(2026, 1, 1), overtime_applies=False, working_day_multiplier=D("1.25"),
                      weekly_off_multiplier=D("1.5"), holiday_multiplier=D("1.5"), all_hours_on_days_off=True,
                      monthly_divisor=30)
        page = self.labor()
        self.assertContains(page, "IV One")
        self.assertContains(page, "&times;1.25")
        self.assertContains(page, "No overtime paid")
        self.assertContains(page, self.url("labor_overtime_rules_new"))
        self.client.force_login(self.viewer)                         # no right to see Labor settings at all
        self.assertEqual(self.labor().status_code, 403)

    def test_the_overtime_buttons_follow_the_overtime_rights_not_the_settings_right(self):
        self.grant(self.viewer, "view_settings_labor")
        self.client.force_login(User.objects.get(pk=self.viewer.pk))
        page = self.labor()
        self.assertNotContains(page, self.url("labor_overtime_rules_new"))


class PerCompanyTests(SettingsPageCase):
    """The company switcher appears only for a module that has per-company settings."""

    def setUp(self):
        super().setUp()
        TestModule(self)
        from apps.configuration.defaults import ensure_system_defaults
        ensure_system_defaults()

    def demo(self, **q):
        return self.client.get(self.url("settings_module", "demo"), q)

    def post_demo(self, scope="", **over):
        data = {"s__demo.hours": "8", "s__demo.count": "3", "s__demo.mode": "week"}
        if scope:
            data["company"] = scope
        data.update(over)
        return self.client.post(self.url("settings_module", "demo"), data)

    def test_the_switcher_is_shown_only_when_a_module_needs_it(self):
        self.assertContains(self.demo(), "Default for all companies")
        self.assertNotContains(self.labor(), "Default for all companies")

    def test_a_company_override_applies_to_that_company_and_can_be_reverted(self):
        self.post_demo(**{"s__demo.hours": "9"})                                    # the default for everyone
        self.post_demo(scope=str(self.co.pk), **{"s__demo.hours": "10"})
        self.assertEqual((svc.get("demo.hours", self.co), svc.get("demo.hours", self.other), svc.get("demo.hours")),
                         (D("10"), D("9"), D("9")))
        page = self.demo(company=self.co.pk)
        self.assertContains(page, "Set for IV One")
        self.assertContains(page, "Go back to the default")
        self.assertContains(self.demo(company=self.other.pk), "Following the default")
        self.post_demo(scope=str(self.co.pk), **{"r__demo.hours": "on"})
        self.assertEqual(svc.get("demo.hours", self.co), D("9"))

    def test_settings_that_are_not_per_company_cannot_be_changed_inside_a_company(self):
        page = self.demo(company=self.co.pk)
        self.assertContains(page, "Applies to every company")
        self.post_demo(scope=str(self.co.pk), **{"s__demo.count": "7"})
        self.assertEqual(svc.get("demo.count"), 3)                                  # silently ignored: its box was disabled

    def test_following_the_default_stores_nothing_for_the_company(self):
        self.post_demo(scope=str(self.co.pk))                                       # same values as the default
        self.assertEqual(SettingValue.objects.filter(company=self.co).count(), 0)

    def test_a_company_the_person_cannot_see_is_refused_and_never_treated_as_everyone(self):
        stranger = Company.objects.create(code="IV3", name="IV Three")
        self.grant(self.hr, "edit_settings_demo")
        self.client.force_login(User.objects.get(pk=self.hr.pk))
        self.assertEqual(self.demo(company=stranger.pk).status_code, 404)
        self.assertEqual(self.post_demo(scope=str(stranger.pk), **{"s__demo.hours": "12"}).status_code, 404)
        self.assertEqual(SettingValue.objects.count(), 0)                            # not even the default changed


class AccessPageTests(SettingsPageCase):
    def test_the_grid_lists_groups_and_rights_and_what_is_granted_now(self):
        page = self.client.get(self.url("settings_access"))
        self.assertEqual(page.status_code, 200)
        for text in ("HR", "Finance", "Management", "Approve or reject overtime claims", "Change Labor settings"):
            self.assertContains(page, text)
        hr = Group.objects.get(name="HR")
        self.assertContains(page, f'value="labor.add_overtimeclaim|{hr.pk}" checked')
        self.assertNotContains(page, f'value="configuration.edit_settings_labor|{hr.pk}" checked')

    def test_ticking_and_saving_gives_the_right_and_unticking_takes_it_back(self):
        hr = Group.objects.get(name="HR")
        _, rows = __import__("apps.configuration.access", fromlist=["grid"]).grid()
        keep = [f"{r.perm}|{g}" for r in rows for g in r.granted]
        r = self.client.post(self.url("settings_access"), {"grant": keep + [f"configuration.edit_settings_labor|{hr.pk}"]})
        self.assertRedirects(r, self.url("settings_access"), fetch_redirect_response=False)
        self.assertContains(self.client.get(r.url), "1 right given")
        self.assertTrue(hr.permissions.filter(codename="edit_settings_labor").exists())
        self.client.force_login(User.objects.get(pk=self.hr.pk))
        self.assertEqual(self.labor().status_code, 200)                              # HR can now change Labor settings
        self.client.force_login(self.root)
        self.client.post(self.url("settings_access"), {"grant": keep})
        self.assertFalse(hr.permissions.filter(codename="edit_settings_labor").exists())

    def test_nothing_ticked_changed_is_reported(self):
        _, rows = __import__("apps.configuration.access", fromlist=["grid"]).grid()
        keep = [f"{r.perm}|{g}" for r in rows for g in r.granted]
        r = self.client.post(self.url("settings_access"), {"grant": keep})
        self.assertContains(self.client.get(r.url), "Nothing changed")

    def test_junk_in_the_form_is_ignored(self):
        self.client.post(self.url("settings_access"), {"grant": ["nonsense", "|", "a|b", "x.y|9999"]})
        self.assertEqual(self.client.get(self.url("settings_access")).status_code, 200)

    def test_the_template_uses_only_the_current_styles(self):
        import re
        from pathlib import Path
        legacy = re.compile(r'class="(pill|muted|err|ok|big|head|cards|chips|scroll|pager|filters|inline-form|form|card narrow)[ "]')
        folder = Path(__file__).resolve().parent.parent / "templates" / "web" / "settings"
        for path in sorted(folder.glob("*.html")):
            text = path.read_text(encoding="utf-8")
            self.assertIsNone(legacy.search(text), path.name)
            self.assertNotIn("<table>", text, path.name)
