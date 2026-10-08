from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.attendance.models import Attendance
from apps.labor import deployment
from apps.scheduling.models import Holiday

from .test_labor_sites import SitePageCase

SUN = date(2026, 3, 1)                    # a Sunday; Friday 6 March is the weekly off


class AttendancePageCase(SitePageCase):
    def setUp(self):
        super().setUp()
        self.place(on=date(2026, 1, 5))                               # Ravi: P1 / Site One from 5 Jan
        self.b = self.worker("Imran")
        self.place(profile=self.b, on=date(2026, 1, 5))
        self.site_key = f"{self.p1.pk}:{self.site1.pk}"

    def page(self, **q):
        q = {"period": "week", "date": "2026-03-04", "site": self.site_key, **q}
        return self.client.get(self.url("labor_attendance"), q)

    def save(self, fill="", **extra):
        data = {"period": "week", "date": "2026-03-04", "site": self.site_key, "fill": fill, **extra}
        return self.client.post(self.url("labor_attendance"), data)

    def cell(self, profile, d):
        return f"c_{profile.employee_id}_{d:%Y%m%d}"


class AccessTests(AttendancePageCase):
    def test_needs_login_and_permission(self):
        self.client.logout()
        self.assertEqual(self.page().status_code, 302)
        self.client.force_login(self.nobody)
        self.assertEqual(self.page().status_code, 403)
        self.assertEqual(self.save(fill="present").status_code, 403)

    def test_finance_and_management_can_look_but_not_enter(self):
        for user in (self.finance, self.boss):
            self.client.force_login(user)
            page = self.page()
            self.assertEqual(page.status_code, 200, user.username)
            self.assertNotContains(page, "<select name=\"c_")
            self.assertNotContains(page, "Save entries")
            self.assertEqual(self.save(fill="present").status_code, 403)
        self.assertEqual(Attendance.objects.count(), 0)

    def test_a_user_only_sees_their_own_companies_sites(self):
        self.client.force_login(self.outsider)
        page = self.page()
        self.assertContains(page, "No workers were allocated")
        self.assertNotContains(page, "Ravi")
        self.assertEqual(self.save(fill="present").status_code, 302)          # nothing to save for them
        self.assertEqual(Attendance.objects.count(), 0)


class SheetPageTests(AttendancePageCase):
    def test_week_grid_shows_every_worker_and_day(self):
        page = self.page()
        self.assertContains(page, "Ravi")
        self.assertContains(page, "Imran")
        self.assertEqual(len(page.context["sheet"].days), 7)
        self.assertContains(page, "01 Mar &ndash; 07 Mar 2026")
        self.assertContains(page, f'name="{self.cell(self.p, SUN)}"')

    def test_month_period_covers_the_whole_month(self):
        page = self.page(period="month")
        self.assertEqual((page.context["first"], page.context["last"]), (date(2026, 3, 1), date(2026, 3, 31)))
        self.assertEqual(len(page.context["sheet"].days), 31)

    def test_day_period(self):
        page = self.page(period="day", date="2026-03-04")
        self.assertEqual(len(page.context["sheet"].days), 1)

    def test_defaults_to_this_week_and_the_first_site_and_clamps_the_future(self):
        page = self.client.get(self.url("labor_attendance"), {"date": "2999-01-01"})
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.context["anchor"], timezone.localdate())
        self.assertEqual(page.context["period"], "week")
        junk = self.client.get(self.url("labor_attendance"), {"period": "century", "date": "not-a-date", "site": "x:y"})
        self.assertEqual(junk.status_code, 200)

    def test_previous_and_next_links_and_no_next_beyond_today(self):
        page = self.page()
        self.assertIn("date=2026-02-22", page.context["prev_query"])
        self.assertIn("date=2026-03-08", page.context["next_query"])
        self.assertFalse(page.context["next_is_future"])
        this_week = self.client.get(self.url("labor_attendance"))
        self.assertTrue(this_week.context["next_is_future"])
        self.assertNotContains(this_week, 'aria-label="Next period"')

    def test_site_dropdown_lists_each_site_with_workers_in_the_period(self):
        self.place(profile=self.b, project=self.p2, location=self.site2, on=date(2026, 3, 4))
        page = self.page()
        self.assertEqual([(p.code, l.code) for p, l in page.context["sites"]], [("P1", "S1"), ("P2", "S2")])
        other = self.page(site=f"{self.p2.pk}:{self.site2.pk}")
        self.assertEqual(other.context["chosen"], (self.p2, self.site2))
        self.assertContains(other, "Imran")
        self.assertNotContains(other, "Ravi")

    def test_weekly_offs_are_shaded_and_a_worker_who_moved_shows_dashes_for_the_other_site(self):
        page = self.page()
        self.assertContains(page, "table-secondary")
        self.place(profile=self.b, project=self.p2, location=self.site2, on=date(2026, 3, 4))
        row = next(r for r in self.page().context["sheet"].rows if r.employee == self.b.employee)
        self.assertEqual([c.covered for c in row.cells], [True, True, True, False, False, False, False])


class SavePageTests(AttendancePageCase):
    def test_save_with_fill_and_exceptions_then_see_it(self):
        r = self.save(fill="present", **{self.cell(self.p, date(2026, 3, 2)): "absent",
                                         self.cell(self.b, date(2026, 3, 3)): "half_day"})
        self.assertRedirects(r, self.url("labor_attendance") + "?period=week&date=2026-03-04&site=" + self.site_key.replace(":", "%3A"),
                             fetch_redirect_response=False)
        status = lambda p, d: Attendance.objects.get(employee=p.employee, date=d).status
        self.assertEqual(status(self.p, date(2026, 3, 2)), "absent")
        self.assertEqual(status(self.b, date(2026, 3, 3)), "half_day")
        self.assertEqual(status(self.p, date(2026, 3, 1)), "present")
        self.assertFalse(Attendance.objects.filter(date=date(2026, 3, 6)).exists())           # the Friday off
        self.assertEqual(Attendance.objects.count(), 2 * 6)
        rec = Attendance.objects.get(employee=self.p.employee, date=date(2026, 3, 1))
        self.assertEqual((rec.project, rec.location, rec.source), (self.p1, self.site1, "bulk"))
        page = self.client.get(r.url)                        # the messages show once, on the page we land on
        self.assertContains(page, "Saved 12 entries")
        self.assertContains(page, "10 present")
        self.assertContains(page, "1 absent")
        self.assertContains(page, "1 half day")

    def test_saving_again_says_there_is_nothing_to_save(self):
        self.save(fill="present")
        r = self.client.post(self.url("labor_attendance"), {"period": "week", "date": "2026-03-04",
                                                            "site": self.site_key, "fill": "present"}, follow=True)
        self.assertContains(r, "Nothing to save")
        self.assertEqual(Attendance.objects.count(), 12)

    def test_nothing_chosen_saves_nothing(self):
        r = self.save(follow=False)
        self.assertEqual(Attendance.objects.count(), 0)
        self.assertContains(self.client.get(r.url), "Save entries")

    def test_a_bad_fill_is_refused_with_a_message(self):
        r = self.client.post(self.url("labor_attendance"), {"period": "week", "date": "2026-03-04",
                                                            "site": self.site_key, "fill": "holiday"}, follow=True)
        self.assertContains(r, "Choose Present, Absent or Half day")
        self.assertEqual(Attendance.objects.count(), 0)

    def test_tampered_cells_are_ignored(self):
        outsider_worker = self.worker("Theirs", company=self.other_co)
        r = self.save(**{f"c_{outsider_worker.employee_id}_20260301": "present",
                         self.cell(self.p, timezone.localdate() + timedelta(days=3)): "present"})
        self.assertEqual(Attendance.objects.count(), 0)
        self.assertEqual(r.status_code, 302)

    def test_saved_days_show_as_letters_and_are_not_offered_again(self):
        self.save(**{self.cell(self.p, SUN): "present", self.cell(self.p, date(2026, 3, 2)): "absent"})
        page = self.page()
        self.assertNotContains(page, f'name="{self.cell(self.p, SUN)}"')
        self.assertContains(page, f'name="{self.cell(self.p, date(2026, 3, 4))}"')
        self.assertContains(page, 'title="Absent"')

    def test_other_pages_link_to_the_sheet(self):
        self.assertContains(self.client.get(self.url("labor_list")), self.url("labor_attendance"))
