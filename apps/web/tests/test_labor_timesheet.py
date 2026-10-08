from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import Permission
from django.utils import timezone

from apps.attendance.models import Attendance
from apps.labor import sitesheet
from apps.labor.timesheet import TimeEntry

from .test_labor_sites import SitePageCase

SUN, MON, TUE = date(2026, 3, 1), date(2026, 3, 2), date(2026, 3, 3)
D = Decimal


class TimesheetPageCase(SitePageCase):
    def setUp(self):
        super().setUp()
        self.place(on=date(2026, 1, 5))
        self.site_key = f"{self.p1.pk}:{self.site1.pk}"
        sitesheet.save_sheet(self.hr, self.p1, self.site1, SUN, date(2026, 3, 7), {}, "present")

    def page(self, **q):
        q = {"period": "week", "date": "2026-03-04", "site": self.site_key, **q}
        return self.client.get(self.url("labor_timesheet"), q)

    def post(self, action="save", fill="", **extra):
        data = {"period": "week", "date": "2026-03-04", "site": self.site_key, "fill": fill, "action": action, **extra}
        return self.client.post(self.url("labor_timesheet"), data)

    def key(self, d, profile=None):
        return f"h_{(profile or self.p).employee_id}_{d:%Y%m%d}"

    def landing(self, response):
        return self.client.get(response.url)                   # messages show once, on the page we land on


class AccessTests(TimesheetPageCase):
    def test_needs_login_and_permission(self):
        self.client.logout()
        self.assertEqual(self.page().status_code, 302)
        self.client.force_login(self.nobody)
        self.assertEqual(self.page().status_code, 403)
        self.assertEqual(self.post(fill="standard").status_code, 403)

    def test_finance_and_management_can_look_but_not_change(self):
        self.post(fill="standard")
        for user in (self.finance, self.boss):
            self.client.force_login(user)
            page = self.page()
            self.assertEqual(page.status_code, 200, user.username)
            self.assertNotContains(page, 'name="h_')
            self.assertNotContains(page, "Save hours")
            self.assertNotContains(page, "Confirm period")
            for action in ("save", "confirm", "reopen"):
                self.assertEqual(self.post(action=action, fill="standard").status_code, 403, (user.username, action))
        self.assertEqual(TimeEntry.objects.filter(status="confirmed").count(), 0)

    def test_hr_can_save_and_confirm_but_not_reopen_without_the_delete_permission(self):
        self.post(fill="standard")
        self.assertEqual(self.post(action="confirm").status_code, 302)
        self.assertEqual(TimeEntry.objects.filter(status="confirmed").count(), 6)
        self.assertEqual(self.post(action="reopen").status_code, 403)
        self.assertEqual(TimeEntry.objects.filter(status="confirmed").count(), 6)
        self.assertNotContains(self.page(), "Reopen")
        self.hr.user_permissions.add(Permission.objects.get(codename="delete_timeentry", content_type__app_label="labor"))
        self.hr = type(self.hr).objects.get(pk=self.hr.pk)
        self.client.force_login(self.hr)
        self.assertContains(self.page(), "Reopen")
        self.landing(self.post(action="reopen"))
        self.assertEqual(TimeEntry.objects.filter(status="confirmed").count(), 0)

    def test_a_user_only_sees_their_own_companies_workers(self):
        self.client.force_login(self.outsider)
        page = self.page()
        self.assertContains(page, "No workers were allocated")
        self.assertNotContains(page, "Ravi")
        self.post(fill="standard")
        self.assertEqual(TimeEntry.objects.count(), 0)


class SheetTests(TimesheetPageCase):
    def test_grid_offers_boxes_only_on_worked_days_with_the_expected_hours_as_a_hint(self):
        page = self.page()
        self.assertContains(page, f'name="{self.key(MON)}"')
        self.assertNotContains(page, f'name="{self.key(date(2026, 3, 6))}"')          # the Friday off
        self.assertContains(page, 'placeholder="8"')
        self.assertContains(page, "Save hours")

    def test_save_with_standard_fill_and_an_overtime_day(self):
        r = self.post(fill="standard", **{self.key(MON): "10.5"})
        self.assertEqual(r.status_code, 302)
        page = self.landing(r)
        self.assertContains(page, "Hours saved: 6 added")
        e = TimeEntry.objects.get(employee=self.p.employee, date=MON)
        self.assertEqual((e.hours, e.overtime_hours, e.project, e.location), (D("10.5"), D("2.5"), self.p1, self.site1))
        self.assertEqual(TimeEntry.objects.filter(employee=self.p.employee).count(), 6)
        page = self.page()
        self.assertContains(page, "border-warning")                                    # the overtime day is outlined
        self.assertEqual(page.context["sheet"].overtime, D("2.5"))
        self.assertEqual(page.context["sheet"].hours, D("10.5") + 5 * 8)

    def test_bad_hours_are_reported_not_saved(self):
        page = self.landing(self.post(**{self.key(MON): "abc", self.key(TUE): "8"}))
        self.assertContains(page, "Not saved")
        self.assertContains(page, "not a number")
        self.assertEqual(TimeEntry.objects.count(), 1)

    def test_nothing_to_save_message(self):
        page = self.landing(self.post())
        self.assertContains(page, "Nothing to save")

    def test_editing_and_clearing_through_the_page(self):
        self.post(fill="standard")
        self.post(**{self.key(MON): "9", self.key(TUE): ""})
        self.assertEqual(TimeEntry.objects.get(employee=self.p.employee, date=MON).hours, D("9"))
        self.assertFalse(TimeEntry.objects.filter(employee=self.p.employee, date=TUE).exists())

    def test_confirm_needs_every_worked_day_filled_then_locks(self):
        self.post(**{self.key(MON): "8"})
        page = self.landing(self.post(action="confirm"))
        self.assertContains(page, "still have no hours")
        self.assertEqual(TimeEntry.objects.filter(status="confirmed").count(), 0)
        self.post(fill="standard")
        page = self.landing(self.post(action="confirm"))
        self.assertContains(page, "Confirmed 6 entries")
        view = self.page()
        self.assertNotContains(view, f'name="{self.key(MON)}"')                       # locked: no box any more
        self.assertContains(view, "Confirmed (locked)")
        self.assertNotContains(view, "Confirm period")

    def test_conflicts_are_flagged_on_the_page_and_block_confirmation(self):
        self.post(fill="standard")
        Attendance.objects.filter(employee=self.p.employee, date=MON).update(status="absent")
        page = self.page()
        self.assertContains(page, "not marked as worked")
        self.assertContains(page, 'title="Hours 8 on a day')
        self.assertContains(self.landing(self.post(action="confirm")), "not marked as worked")

    def test_a_forged_action_or_cell_does_nothing(self):
        self.assertEqual(self.post(action="delete_everything").status_code, 403)
        self.post(**{f"h_999999_20260302": "8", self.key(date(2026, 3, 6)): "8"})
        self.assertEqual(TimeEntry.objects.count(), 0)

    def test_period_controls_and_defaults(self):
        self.assertEqual(len(self.page(period="month").context["sheet"].days), 31)
        junk = self.client.get(self.url("labor_timesheet"), {"period": "x", "date": "no", "site": "q"})
        self.assertEqual(junk.status_code, 200)
        self.assertIn("date=2026-03-08", self.page().context["next_query"])

    def test_tab_is_linked_from_the_labor_pages(self):
        self.assertContains(self.client.get(self.url("labor_list")), self.url("labor_timesheet"))
