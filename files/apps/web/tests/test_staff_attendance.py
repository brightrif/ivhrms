from datetime import date, timedelta
from unittest.mock import patch

from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.attendance import services as att_services
from apps.attendance import staffsheet
from apps.attendance.models import Attendance
from apps.employees.models import Employee
from apps.organization.models import Company, CompanyAccess

key = staffsheet.cell_key


def _kinds(overrides=None):
    """The scheduling answer for these tests: Friday is the weekly off; `overrides` {date: kind} adds more."""
    overrides = overrides or {}

    def fake(emp, first, last):
        out, d = {}, first
        while d <= last:
            out[d] = overrides.get(d, "weekly_off" if d.weekday() == 4 else "working")
            d += timedelta(days=1)
        return out
    return fake


class GridCase(TestCase):
    def setUp(self):
        patcher = patch("apps.attendance.staffsheet.day_types", side_effect=_kinds())
        patcher.start()
        self.addCleanup(patcher.stop)
        self.co = Company.objects.create(code="C1", name="Company One")
        self.hr = User.objects.create_user("hr", password="pw")
        self.hr.user_permissions.add(Permission.objects.get(content_type__app_label="attendance",
                                                            codename="add_attendance"))
        CompanyAccess.objects.create(user=self.hr, company=self.co)
        mk = lambda no, first, **kw: Employee.objects.create(
            company=self.co, employee_no=no, first_name=first, joining_date=date(2020, 1, 1), **kw)
        self.ali, self.sara = mk("S1", "Ali"), mk("S2", "Sara")
        self.worker = mk("L1", "Mason", worker_type="labor")
        self.gone = mk("S9", "Gone", status="separated")
        self.today = timezone.localdate()
        self.first, self.last = staffsheet.period_bounds("week", self.today - timedelta(days=14))   # a whole past week
        self.sun, self.mon, self.tue = self.first, self.first + timedelta(days=1), self.first + timedelta(days=2)
        self.fri = self.first + timedelta(days=5)
        self.client.force_login(self.hr)
        self.url = reverse("web:staff_attendance")

    def post(self, action, cells=None, fill="", period="week", first=None, **extra):
        data = {"action": action, "period": period, "date": (first or self.first).isoformat(), "fill": fill}
        data.update(cells or {})
        data.update(extra)
        return self.client.post(self.url, data)

    def status(self, emp, d):
        rec = Attendance.objects.filter(employee=emp, date=d).first()
        return rec.status if rec else None


class SheetTests(GridCase):
    def get(self, **params):
        params.setdefault("date", self.first.isoformat())
        return self.client.get(self.url, params)

    def test_it_needs_the_right_to_add_attendance(self):
        self.client.force_login(User.objects.create_user("nobody"))
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.post("review", {key(self.ali, self.mon): "present"}).status_code, 403)

    def test_the_week_is_the_default_and_runs_sunday_to_saturday(self):
        resp = self.get()
        self.assertEqual(resp.context["period"], "week")
        self.assertEqual((resp.context["first"], resp.context["last"]), (self.first, self.last))
        self.assertEqual(len(resp.context["sheet"].days), 7)
        self.assertEqual(self.first.weekday(), 6)

    def test_the_month_fits_a_whole_month_and_uses_the_small_cells(self):
        resp = self.get(period="month")
        self.assertEqual(resp.context["size"], "gs-month")
        self.assertEqual(resp.context["ndays"], len(resp.context["sheet"].days))
        self.assertGreaterEqual(resp.context["ndays"], 28)

    def test_the_day_view_has_one_column_and_the_time_boxes(self):
        resp = self.get(period="day", date=self.mon.isoformat())
        self.assertEqual(resp.context["ndays"], 1)
        self.assertContains(resp, f'name="i_{self.ali.pk}"')

    def test_only_staff_who_are_still_here_are_listed(self):
        listed = [r.employee for r in self.get().context["sheet"].rows]
        self.assertEqual(listed, [self.ali, self.sara])                       # not the labour worker, not the leaver

    def test_someone_who_joins_during_the_period_has_blank_days_before_it(self):
        Employee.objects.filter(pk=self.sara.pk).update(joining_date=self.tue)
        row = next(r for r in self.get().context["sheet"].rows if r.employee == self.sara)
        self.assertEqual([c.editable for c in row.cells[:3]], [False, False, True])
        self.assertTrue(row.cells[0].before)

    def test_someone_who_joins_after_the_period_is_not_listed(self):
        Employee.objects.filter(pk=self.sara.pk).update(joining_date=self.last + timedelta(days=1))
        self.assertEqual([r.employee for r in self.get().context["sheet"].rows], [self.ali])

    def test_the_weekly_off_is_marked_per_person_and_can_still_be_given(self):
        cell = self.get().context["sheet"].rows[0].cells[5]
        self.assertEqual((cell.date, cell.kind, cell.editable, cell.rest_row), (self.fri, "weekly_off", True, False))

    def test_a_recorded_day_is_shown_with_no_box(self):
        att_services.mark_attendance(self.ali, self.mon, "present")
        resp = self.get()
        self.assertNotContains(resp, f'name="{key(self.ali, self.mon)}"')
        self.assertContains(resp, f'name="{key(self.sara, self.mon)}"')

    def test_future_days_have_no_box(self):
        first, last = staffsheet.period_bounds("week", self.today)
        sheet = staffsheet.build(self.hr, first, last)
        for cell in sheet.rows[0].cells:
            self.assertEqual(cell.editable, cell.date <= self.today)

    def test_a_period_that_has_not_started_shows_the_current_one(self):
        resp = self.client.get(self.url, {"period": "week", "date": (self.today + timedelta(days=30)).isoformat()})
        self.assertEqual(resp.context["first"], staffsheet.period_bounds("week", self.today)[0])
        self.assertContains(resp, "future date")

    def test_the_search_and_the_one_person_view_filter_the_rows(self):
        self.assertEqual([r.employee for r in self.get(q="sar").context["sheet"].rows], [self.sara])
        self.assertEqual([r.employee for r in self.get(employee=self.ali.pk).context["sheet"].rows], [self.ali])

    def test_next_is_offered_only_while_the_next_period_has_started(self):
        past = self.get()
        self.assertIsNotNone(past.context["next_date"])
        now = self.client.get(self.url, {"period": "week"})
        self.assertIsNone(now.context["next_date"])


class ReviewAndConfirmTests(GridCase):
    def choose(self):
        return {key(self.ali, self.mon): "present", key(self.ali, self.tue): "absent",
                key(self.sara, self.mon): "half_day"}

    def test_the_review_shows_what_will_be_created_and_saves_nothing(self):
        resp = self.post("review", self.choose())
        self.assertContains(resp, "Confirm and save")
        self.assertContains(resp, "3</strong> attendance entries")
        self.assertEqual(len(resp.context["plan"].entries), 3)
        self.assertEqual(Attendance.objects.count(), 0)

    def test_the_review_also_lists_the_rest_days_that_would_be_recorded(self):
        resp = self.post("review", self.choose())
        self.assertEqual(resp.context["plan"].rest_counts, [("weekly offs", 2)])           # Friday, for two people
        self.assertContains(resp, "weekly offs")
        self.assertEqual(Attendance.objects.count(), 0)

    def test_confirming_creates_the_entries_and_the_rest_days_once(self):
        self.assertEqual(self.post("confirm", self.choose()).status_code, 302)
        self.assertEqual((self.status(self.ali, self.mon), self.status(self.ali, self.tue),
                          self.status(self.sara, self.mon)), ("present", "absent", "half_day"))
        self.assertEqual((self.status(self.ali, self.fri), self.status(self.sara, self.fri)), ("weekly_off",) * 2)
        total = Attendance.objects.count()
        self.post("confirm", self.choose())                                                 # pressing it again
        self.assertEqual(Attendance.objects.count(), total)

    def test_the_message_after_saving_says_what_was_saved(self):
        resp = self.client.post(self.url, {"action": "confirm", "period": "week", "date": self.first.isoformat(),
                                           **self.choose()}, follow=True)
        self.assertContains(resp, "Attendance saved: 3 entries")
        self.assertContains(resp, "2 weekly offs")

    def test_nothing_chosen_and_nothing_to_record_means_nothing_to_review(self):
        att_services.mark_attendance(self.ali, self.fri, "present")
        att_services.mark_attendance(self.sara, self.fri, "present")
        resp = self.post("review")
        self.assertContains(resp, "Nothing to save yet")
        self.assertNotContains(resp, "Confirm and save")

    def test_rest_days_alone_can_be_recorded_with_a_confirmation(self):
        resp = self.post("review")                                              # nothing chosen, Fridays have no record
        self.assertContains(resp, "No attendance entries will be created")
        self.assertContains(resp, "Confirm and save")
        self.post("confirm")
        self.assertEqual((self.status(self.ali, self.fri), self.status(self.sara, self.fri)), ("weekly_off",) * 2)

    def test_back_to_edit_keeps_what_was_chosen_and_saves_nothing(self):
        resp = self.post("edit", self.choose())
        row = next(r for r in resp.context["sheet"].rows if r.employee == self.ali)
        self.assertEqual((row.cells[1].value, row.cells[2].value), ("present", "absent"))
        self.assertEqual(Attendance.objects.count(), 0)

    def test_the_review_carries_the_choices_to_the_confirmation(self):
        resp = self.post("review", self.choose())
        self.assertIn((key(self.ali, self.mon), "present"), resp.context["hidden"])


class RuleTests(GridCase):
    def test_fill_gives_every_empty_working_day_but_not_the_rest_day(self):
        self.post("confirm", fill="present")
        self.assertEqual([self.status(self.ali, self.first + timedelta(days=i)) for i in range(7)],
                         ["present"] * 5 + ["weekly_off", "present"])

    def test_a_typed_choice_beats_the_fill_and_the_rest_day_can_be_worked(self):
        self.post("confirm", {key(self.ali, self.mon): "absent", key(self.ali, self.fri): "present"}, fill="present")
        self.assertEqual((self.status(self.ali, self.mon), self.status(self.ali, self.fri)), ("absent", "present"))

    def test_an_existing_entry_is_never_overwritten(self):
        att_services.mark_attendance(self.ali, self.mon, "present")
        self.post("confirm", {key(self.ali, self.mon): "absent", key(self.sara, self.mon): "present"})   # a forged box
        self.assertEqual((self.status(self.ali, self.mon), self.status(self.sara, self.mon)), ("present", "present"))
        self.assertEqual(Attendance.objects.filter(employee=self.ali, date=self.mon).count(), 1)

    def test_the_systems_rest_day_row_can_be_replaced_when_someone_worked(self):
        self.post("confirm")                                                   # records the Fridays
        resp = self.get_sheet()
        cell = next(r for r in resp.context["sheet"].rows if r.employee == self.ali).cells[5]
        self.assertTrue(cell.rest_row)
        self.assertTrue(cell.editable)
        review = self.post("review", {key(self.ali, self.fri): "present"})
        self.assertEqual(review.context["plan"].rest_worked, 1)
        self.assertContains(review, "replace a weekly off")
        self.assertEqual(self.status(self.ali, self.fri), "weekly_off")        # not yet
        self.post("confirm", {key(self.ali, self.fri): "present"})
        rec = Attendance.objects.get(employee=self.ali, date=self.fri)
        self.assertEqual((rec.status, rec.source), ("present", "manual"))
        self.assertEqual(self.status(self.sara, self.fri), "weekly_off")       # the others are untouched

    def test_a_row_a_person_entered_is_not_a_rest_day_row_and_stays_locked(self):
        att_services.mark_attendance(self.ali, self.fri, "absent")
        cell = next(r for r in staffsheet.build(self.hr, self.first, self.last).rows if r.employee == self.ali).cells[5]
        self.assertFalse(cell.editable)
        self.post("confirm", {key(self.ali, self.fri): "present"})
        self.assertEqual(self.status(self.ali, self.fri), "absent")

    def test_a_holiday_is_recorded_as_a_holiday(self):
        with patch("apps.attendance.staffsheet.day_types", side_effect=_kinds({self.tue: "holiday"})):
            self.post("confirm")
        self.assertEqual(self.status(self.ali, self.tue), "holiday")
        self.assertEqual(self.status(self.ali, self.fri), "weekly_off")

    def test_fill_leaves_a_holiday_alone_too(self):
        with patch("apps.attendance.staffsheet.day_types", side_effect=_kinds({self.tue: "holiday"})):
            self.post("confirm", fill="present")
        self.assertEqual(self.status(self.ali, self.tue), "holiday")
        self.assertEqual(self.status(self.ali, self.mon), "present")

    def test_a_rest_day_before_joining_is_not_recorded(self):
        Employee.objects.filter(pk=self.ali.pk).update(joining_date=self.last)
        self.post("confirm")
        self.assertEqual(Attendance.objects.filter(employee=self.ali).count(), 0)

    def test_the_times_belong_to_the_day_view_only(self):
        self.post("confirm", {key(self.ali, self.mon): "present", f"i_{self.ali.pk}": "08:00", f"o_{self.ali.pk}": "17:00"})
        rec = Attendance.objects.get(employee=self.ali, date=self.mon)
        self.assertIsNone(rec.check_in)                                       # the week view ignores time boxes
        self.post("confirm", {key(self.ali, self.tue): "present", f"i_{self.ali.pk}": "08:00", f"o_{self.ali.pk}": "17:00"},
                  period="day", first=self.tue)
        rec = Attendance.objects.get(employee=self.ali, date=self.tue)
        self.assertEqual((timezone.localtime(rec.check_in).strftime("%H:%M"),
                          timezone.localtime(rec.check_out).strftime("%H:%M")), ("08:00", "17:00"))

    def test_a_checkout_before_the_checkin_is_left_out_and_said_so(self):
        resp = self.post("review", {key(self.ali, self.mon): "present", f"i_{self.ali.pk}": "17:00",
                                    f"o_{self.ali.pk}": "08:00", key(self.sara, self.mon): "present"},
                         period="day", first=self.mon)
        self.assertEqual([e.employee for e in resp.context["plan"].entries], [self.sara])
        self.assertContains(resp, "Check-out must be after check-in")
        self.assertContains(resp, "will be left out")

    def test_a_badly_typed_time_is_left_out_and_said_so(self):
        resp = self.post("review", {key(self.ali, self.mon): "present", f"i_{self.ali.pk}": "8 o'clock",
                                    key(self.sara, self.mon): "present"}, period="day", first=self.mon)
        self.assertContains(resp, "is not a time")
        self.assertEqual(len(resp.context["plan"].entries), 1)

    def test_an_absent_entry_drops_any_times_typed_with_it(self):
        self.post("confirm", {key(self.ali, self.mon): "absent", f"i_{self.ali.pk}": "08:00"}, period="day", first=self.mon)
        rec = Attendance.objects.get(employee=self.ali, date=self.mon)
        self.assertEqual((rec.status, rec.check_in), ("absent", None))

    def test_a_future_period_posted_by_hand_saves_nothing(self):
        later = self.today + timedelta(days=20)
        resp = self.post("confirm", {key(self.ali, later): "present"}, first=later)
        self.assertEqual(Attendance.objects.count(), 0)
        self.assertContains(resp, "Nothing was saved")

    def test_the_fill_choice_must_be_one_the_sheet_offers(self):
        resp = self.post("review", fill="paid_leave")
        self.assertContains(resp, "Choose Present, Absent or Half day")
        self.assertEqual(Attendance.objects.count(), 0)

    def test_the_sheet_only_reaches_companies_the_person_may_see(self):
        other = Company.objects.create(code="C2", name="Company Two")
        stranger = Employee.objects.create(company=other, employee_no="X1", first_name="Zed", joining_date=date(2020, 1, 1))
        self.post("confirm", {key(stranger, self.mon): "present"})
        self.assertEqual(Attendance.objects.filter(employee=stranger).count(), 0)

    def get_sheet(self):
        return self.client.get(self.url, {"period": "week", "date": self.first.isoformat()})
