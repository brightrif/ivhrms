from datetime import date

from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.organization.models import Company, CompanyAccess
from apps.scheduling import calendar_rules as rules
from apps.scheduling.models import Holiday
from apps.scheduling.weekly_off import WeeklyOffRule


class CalendarPagesCase(TestCase):
    def setUp(self):
        self.co = Company.objects.create(code="C1", name="Company One")
        self.other = Company.objects.create(code="C2", name="Company Two")
        self.hr = self.person("hr", "HR", self.co)
        self.boss = self.person("boss", "Management", self.co)
        self.root = User.objects.create_superuser("root", password="pw")
        self.client.force_login(self.hr)

    def person(self, name, group, company):
        user = User.objects.create_user(name, password="pw")
        user.groups.add(Group.objects.get(name=group))
        CompanyAccess.objects.create(user=user, company=company)
        return user

    def add(self, **over):
        data = {"first_date": "2026-12-16", "last_date": "", "name": "National Day", "name_ar": "",
                "applies_to": str(self.co.pk)}
        data.update(over)
        return self.client.post(reverse("web:holiday_add"), data)


class RightsTests(CalendarPagesCase):
    def test_hr_can_keep_the_calendar_and_management_can_only_look(self):
        for perm in ("view_holiday", "add_holiday", "change_holiday", "view_weeklyoffrule", "add_weeklyoffrule"):
            self.assertTrue(self.hr.has_perm(f"scheduling.{perm}"), perm)
        self.assertTrue(self.boss.has_perm("scheduling.view_holiday"))
        self.assertFalse(self.boss.has_perm("scheduling.add_holiday"))
        self.assertFalse(self.boss.has_perm("scheduling.add_weeklyoffrule"))

    def test_someone_with_no_right_gets_a_403(self):
        self.client.force_login(User.objects.create_user("nobody"))
        for name in ("holiday_list", "holiday_add", "weekly_off"):
            self.assertEqual(self.client.get(reverse(f"web:{name}")).status_code, 403, name)

    def test_management_sees_the_pages_but_cannot_change_anything(self):
        self.client.force_login(self.boss)
        self.assertEqual(self.client.get(reverse("web:holiday_list")).status_code, 200)
        self.assertEqual(self.client.get(reverse("web:holiday_add")).status_code, 403)
        resp = self.client.post(reverse("web:weekly_off"), {"company": self.co.pk, "days": [4, 5], "effective_from": "2027-01-01"})
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(WeeklyOffRule.objects.exists())


class HolidayPageTests(CalendarPagesCase):
    def test_a_one_day_holiday_is_added_and_listed(self):
        resp = self.add()
        self.assertRedirects(resp, reverse("web:holiday_list") + "?year=2026")
        self.assertContains(self.client.get(reverse("web:holiday_list"), {"year": 2026}), "National Day")

    def test_a_range_makes_one_row_per_day(self):
        self.add(first_date="2026-03-20", last_date="2026-03-23", name="Eid")
        self.assertEqual(Holiday.objects.filter(name="Eid").count(), 4)

    def test_a_day_that_has_one_is_refused_with_the_date(self):
        self.add()
        resp = self.add()
        self.assertContains(resp, "16 Dec 2026")
        self.assertEqual(Holiday.objects.count(), 1)

    def test_the_last_day_cannot_come_before_the_first(self):
        self.assertContains(self.add(first_date="2026-03-23", last_date="2026-03-20"), "cannot be before")

    def test_only_a_superuser_can_add_a_holiday_for_every_company(self):
        self.assertContains(self.add(applies_to="all"), "Select a valid choice")
        self.assertFalse(Holiday.objects.exists())
        self.client.force_login(self.root)
        self.add(applies_to="all")
        self.assertTrue(Holiday.objects.filter(company__isnull=True, name="National Day").exists())

    def test_hr_cannot_add_for_a_company_it_cannot_see(self):
        self.assertContains(self.add(applies_to=str(self.other.pk)), "Select a valid choice")
        self.assertFalse(Holiday.objects.exists())

    def test_a_holiday_can_be_renamed_and_switched_off_and_on(self):
        self.add()
        h = Holiday.objects.get()
        self.client.post(reverse("web:holiday_edit", args=[h.pk]), {"name": "National Day 2026", "name_ar": ""})
        self.client.post(reverse("web:holiday_toggle", args=[h.pk]))
        h.refresh_from_db()
        self.assertEqual((h.name, h.is_active), ("National Day 2026", False))
        self.client.post(reverse("web:holiday_toggle", args=[h.pk]))
        h.refresh_from_db()
        self.assertTrue(h.is_active)

    def test_hr_cannot_change_a_holiday_for_every_company_or_another_company(self):
        everyone = rules.add_holidays(None, date(2026, 12, 16), None, "Global")[0]
        elsewhere = rules.add_holidays(self.other, date(2026, 12, 17), None, "Elsewhere")[0]
        for h in (everyone, elsewhere):
            self.assertEqual(self.client.post(reverse("web:holiday_toggle", args=[h.pk])).status_code, 403)
            self.assertEqual(self.client.get(reverse("web:holiday_edit", args=[h.pk])).status_code, 403)

    def test_the_list_shows_global_holidays_and_own_company_ones_only(self):
        rules.add_holidays(None, date(2026, 12, 16), None, "Global")
        rules.add_holidays(self.co, date(2026, 12, 17), None, "Mine")
        rules.add_holidays(self.other, date(2026, 12, 18), None, "Theirs")
        resp = self.client.get(reverse("web:holiday_list"), {"year": 2026})
        self.assertContains(resp, "Global")
        self.assertContains(resp, "Mine")
        self.assertNotContains(resp, "Theirs")

    def test_a_year_that_is_not_a_year_falls_back_to_this_one(self):
        self.assertEqual(self.client.get(reverse("web:holiday_list"), {"year": "abc"}).status_code, 200)


class WeeklyOffPageTests(CalendarPagesCase):
    def post(self, **over):
        data = {"company": self.co.pk, "days": [4, 5], "effective_from": "2027-01-01"}
        data.update(over)
        return self.client.post(reverse("web:weekly_off"), data)

    def test_the_page_shows_friday_until_a_rule_is_set(self):
        resp = self.client.get(reverse("web:weekly_off"))
        self.assertContains(resp, "Friday")
        self.assertContains(resp, "No rule has been set")

    def test_hr_sets_a_new_weekly_off_that_starts_on_a_date(self):
        self.assertRedirects(self.post(), reverse("web:weekly_off"))
        self.assertEqual(rules.weekly_off_on(self.co.pk, date(2027, 1, 2)), [4, 5])
        self.assertEqual(rules.weekly_off_on(self.co.pk, date(2026, 12, 25)), [4])           # earlier days keep theirs
        self.assertContains(self.client.get(reverse("web:weekly_off")), "Friday, Saturday")

    def test_a_bad_choice_is_explained_and_nothing_is_stored(self):
        self.assertContains(self.post(days=[]), "This field is required")
        self.assertContains(self.post(days=[0, 1, 2, 3, 4, 5, 6]), "at least one working day")
        self.assertFalse(WeeklyOffRule.objects.exists())

    def test_hr_cannot_set_it_for_a_company_it_cannot_see(self):
        self.assertContains(self.post(company=self.other.pk), "Select a valid choice")
        self.assertFalse(WeeklyOffRule.objects.exists())

    def test_the_settings_tab_for_holidays_and_weekly_off_describes_itself(self):
        self.client.force_login(self.root)
        resp = self.client.get(reverse("web:settings_module", args=["scheduling"]))
        self.assertContains(resp, "Weekly off")
        self.assertContains(resp, "Public holidays")
