from datetime import date
from decimal import Decimal

from django.contrib.auth.models import Permission

from apps.configuration import registry
from apps.configuration import services as svc

from .test_labor_sites import SitePageCase

D = Decimal


class LaborScreensFollowSettings(SitePageCase):
    def setUp(self):
        super().setUp()
        self.place(on=date(2026, 1, 5))
        self.site_key = f"{self.p1.pk}:{self.site1.pk}"

    def test_every_right_offered_on_the_access_grid_exists(self):
        """A typo in settings_spec.py would silently drop a row from the grid, so check each one resolves."""
        for module in registry.modules():
            for cap in module.capabilities:
                app_label, codename = cap.perm.split(".", 1)
                self.assertTrue(Permission.objects.filter(content_type__app_label=app_label, codename=codename).exists(),
                                cap.perm)

    def test_the_add_worker_form_starts_with_the_chosen_defaults(self):
        form = self.client.get(self.url("labor_create")).context["form"]
        self.assertEqual((form.initial["standard_hours"], form.initial["overtime_eligible"]), (D("8"), True))
        svc.set_value("labor.default_standard_hours", "9")
        svc.set_value("labor.new_worker_overtime_eligible", "")
        form = self.client.get(self.url("labor_create")).context["form"]
        self.assertEqual((form.initial["standard_hours"], form.initial["overtime_eligible"]), (D("9"), False))
        self.assertContains(self.client.get(self.url("labor_create")), 'value="9"')

    def test_an_explicit_choice_on_the_form_still_wins(self):
        svc.set_value("labor.default_standard_hours", "9")
        self.new_worker_post(standard_hours="7.5")
        from apps.labor.models import LaborProfile
        self.assertEqual(LaborProfile.objects.latest("pk").current_rate.standard_hours, D("7.5"))

    def test_the_setup_form_for_an_existing_labor_employee_uses_them_too(self):
        from apps.employees.models import Employee
        e = Employee.objects.create(company=self.co, first_name="Old", worker_type="labor", joining_date=date(2025, 5, 1))
        svc.set_value("labor.default_standard_hours", "10")
        form = self.client.get(self.url("labor_setup", e.pk)).context["form"]
        self.assertEqual(form.initial["standard_hours"], D("10"))

    def test_the_sheets_open_on_the_chosen_period_unless_one_is_asked_for(self):
        for name in ("labor_attendance", "labor_timesheet"):
            self.assertEqual(self.client.get(self.url(name)).context["period"], "week", name)
        svc.set_value("labor.sheet_period", "month")
        for name in ("labor_attendance", "labor_timesheet"):
            self.assertEqual(self.client.get(self.url(name)).context["period"], "month", name)
            self.assertEqual(self.client.get(self.url(name), {"period": "day"}).context["period"], "day", name)
        self.assertEqual(self.client.get(self.url("labor_overtime")).context["period"], "month")      # always the month

    def test_the_fill_choice_starts_chosen_when_the_setting_says_so(self):
        params = {"period": "week", "date": "2026-03-04", "site": self.site_key}
        att, ts = self.url("labor_attendance"), self.url("labor_timesheet")
        self.assertNotContains(self.client.get(att, params), '<option value="present" selected>')
        self.assertNotContains(self.client.get(ts, params), '<option value="standard" selected>')
        svc.set_value("labor.attendance_fill_present", "on")
        svc.set_value("labor.timesheet_fill_standard", "on")
        self.assertContains(self.client.get(att, params), '<option value="present" selected>')
        self.assertContains(self.client.get(ts, params), '<option value="standard" selected>')

    def test_a_preselected_fill_does_nothing_until_the_page_is_saved(self):
        svc.set_value("labor.attendance_fill_present", "on")
        from apps.attendance.models import Attendance
        self.client.get(self.url("labor_attendance"), {"period": "week", "date": "2026-03-04", "site": self.site_key})
        self.assertEqual(Attendance.objects.count(), 0)
