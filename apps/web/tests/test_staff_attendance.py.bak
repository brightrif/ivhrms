from datetime import date, timedelta
from unittest.mock import patch

from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.attendance import services as att_services
from apps.attendance.models import Attendance
from apps.employees.models import Employee
from apps.organization.models import Company, CompanyAccess


def _kinds(overrides=None):
    """The scheduling answer for these tests: every day is a working day, except the ones named."""
    overrides = overrides or {}

    def fake(emp, first, last):
        out, d = {}, first
        while d <= last:
            out[d] = overrides.get(emp.employee_no, "working")
            d += timedelta(days=1)
        return out
    return fake


class SheetCase(TestCase):
    def setUp(self):
        patcher = patch("apps.attendance.staffsheet.day_types", side_effect=_kinds())
        self.kinds = patcher.start()
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
        self.day = self.today - timedelta(days=1)
        self.client.force_login(self.hr)
        self.url = reverse("web:staff_attendance")

    def post(self, action, fill="", day=None, **chosen):
        data = {"action": action, "date": (day or self.day).isoformat(), "fill": fill}
        data.update(chosen)
        return self.client.post(self.url, data)

    def status(self, emp):
        rec = Attendance.objects.filter(employee=emp, date=self.day).first()
        return rec.status if rec else None


class SheetTests(SheetCase):
    def test_it_needs_the_right_to_add_attendance(self):
        self.client.force_login(User.objects.create_user("nobody"))
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.post("review", **{f"s_{self.ali.pk}": "present"}).status_code, 403)

    def test_only_staff_who_are_still_here_are_listed(self):
        resp = self.client.get(self.url, {"date": self.day.isoformat()})
        listed = [r.employee for r in resp.context["rows"]]
        self.assertEqual(listed, [self.ali, self.sara])                 # not the labour worker, not the leaver

    def test_someone_who_joins_later_is_not_listed(self):
        Employee.objects.filter(pk=self.sara.pk).update(joining_date=self.today)
        listed = [r.employee for r in self.client.get(self.url, {"date": self.day.isoformat()}).context["rows"]]
        self.assertEqual(listed, [self.ali])

    def test_a_future_day_shows_today_with_a_warning(self):
        resp = self.client.get(self.url, {"date": (self.today + timedelta(days=3)).isoformat()}, follow=True)
        self.assertEqual(resp.context["d"], self.today)
        self.assertContains(resp, "future date")

    def test_a_recorded_day_is_shown_but_has_no_boxes(self):
        att_services.mark_attendance(self.ali, self.day, "present")
        resp = self.client.get(self.url, {"date": self.day.isoformat()})
        self.assertContains(resp, "Already recorded")
        self.assertNotContains(resp, f'name="s_{self.ali.pk}"')
        self.assertContains(resp, f'name="s_{self.sara.pk}"')

    def test_one_person_can_be_shown_alone(self):
        resp = self.client.get(self.url, {"date": self.day.isoformat(), "employee": self.sara.pk})
        self.assertEqual([r.employee for r in resp.context["rows"]], [self.sara])

    def test_the_search_filters_by_name_or_number(self):
        self.assertEqual([r.employee for r in self.client.get(self.url, {"q": "sar"}).context["rows"]], [self.sara])
        self.assertEqual([r.employee for r in self.client.get(self.url, {"q": "S1"}).context["rows"]], [self.ali])


class ReviewAndConfirmTests(SheetCase):
    def test_the_review_shows_what_will_be_created_and_saves_nothing(self):
        resp = self.post("review", **{f"s_{self.ali.pk}": "present", f"s_{self.sara.pk}": "absent"})
        self.assertContains(resp, "Confirm and save")
        self.assertContains(resp, "2 attendance entries")
        self.assertContains(resp, "Ali")
        self.assertEqual(len(resp.context["plan"].entries), 2)
        self.assertEqual(Attendance.objects.count(), 0)

    def test_confirming_creates_the_entries_once(self):
        data = {f"s_{self.ali.pk}": "present", f"s_{self.sara.pk}": "half_day"}
        resp = self.post("confirm", **data)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual((self.status(self.ali), self.status(self.sara)), ("present", "half_day"))
        self.post("confirm", **data)                                          # pressing it again changes nothing
        self.assertEqual(Attendance.objects.count(), 2)

    def test_the_message_after_saving_says_what_was_saved(self):
        resp = self.client.post(self.url, {"action": "confirm", "date": self.day.isoformat(), "fill": "",
                                           f"s_{self.ali.pk}": "present"}, follow=True)
        self.assertContains(resp, "Attendance saved")
        self.assertContains(resp, "1 entry")

    def test_nothing_chosen_means_nothing_to_review(self):
        resp = self.post("review")
        self.assertContains(resp, "Nothing to save yet")
        self.assertNotContains(resp, "Confirm and save")

    def test_back_to_edit_keeps_what_was_chosen(self):
        resp = self.post("edit", **{f"s_{self.ali.pk}": "absent", f"i_{self.sara.pk}": "08:00"})
        by = {r.employee.pk: r for r in resp.context["rows"]}
        self.assertEqual(by[self.ali.pk].status, "absent")
        self.assertEqual(by[self.sara.pk].check_in, "08:00")
        self.assertEqual(Attendance.objects.count(), 0)

    def test_the_review_carries_the_choices_to_the_confirmation(self):
        resp = self.post("review", **{f"s_{self.ali.pk}": "present", f"i_{self.ali.pk}": "08:05"})
        self.assertIn((f"s_{self.ali.pk}", "present"), resp.context["hidden"])
        self.assertIn((f"i_{self.ali.pk}", "08:05"), resp.context["hidden"])


class RuleTests(SheetCase):
    def test_fill_gives_every_empty_working_day_but_not_rest_days(self):
        with patch("apps.attendance.staffsheet.day_types", side_effect=_kinds({"S2": "weekly_off"})):
            resp = self.post("review", fill="present")
            self.assertEqual([e.employee for e in resp.context["plan"].entries], [self.ali])
            self.post("confirm", fill="present")
        self.assertEqual((self.status(self.ali), self.status(self.sara)), ("present", None))

    def test_a_typed_choice_beats_the_fill_and_a_rest_day_can_be_worked(self):
        with patch("apps.attendance.staffsheet.day_types", side_effect=_kinds({"S2": "weekly_off"})):
            self.post("confirm", fill="present", **{f"s_{self.ali.pk}": "absent", f"s_{self.sara.pk}": "present"})
        self.assertEqual((self.status(self.ali), self.status(self.sara)), ("absent", "present"))

    def test_an_existing_entry_is_never_overwritten(self):
        att_services.mark_attendance(self.ali, self.day, "present")
        self.post("confirm", **{f"s_{self.ali.pk}": "absent", f"s_{self.sara.pk}": "present"})   # a forged box for Ali
        self.assertEqual(self.status(self.ali), "present")
        self.assertEqual(self.status(self.sara), "present")
        self.assertEqual(Attendance.objects.filter(employee=self.ali).count(), 1)

    def test_a_checkout_before_the_checkin_is_left_out_and_said_so(self):
        resp = self.post("review", **{f"s_{self.ali.pk}": "present", f"i_{self.ali.pk}": "17:00",
                                      f"o_{self.ali.pk}": "08:00", f"s_{self.sara.pk}": "present"})
        self.assertEqual([e.employee for e in resp.context["plan"].entries], [self.sara])
        self.assertContains(resp, "Check-out must be after check-in")
        self.assertContains(resp, "will be left out")

    def test_a_badly_typed_time_is_left_out_and_said_so(self):
        resp = self.post("review", **{f"s_{self.ali.pk}": "present", f"i_{self.ali.pk}": "8 o'clock",
                                      f"s_{self.sara.pk}": "present"})
        self.assertContains(resp, "is not a time")
        self.assertEqual(len(resp.context["plan"].entries), 1)

    def test_times_are_saved_and_lateness_is_worked_out_by_the_service(self):
        self.post("confirm", **{f"s_{self.ali.pk}": "present", f"i_{self.ali.pk}": "08:00", f"o_{self.ali.pk}": "17:00"})
        rec = Attendance.objects.get(employee=self.ali, date=self.day)
        self.assertEqual((timezone.localtime(rec.check_in).strftime("%H:%M"),
                          timezone.localtime(rec.check_out).strftime("%H:%M")), ("08:00", "17:00"))
        self.assertEqual(rec.source, "manual")                                  # one person: entered by hand

    def test_an_absent_entry_drops_any_times_typed_with_it(self):
        self.post("confirm", **{f"s_{self.ali.pk}": "absent", f"i_{self.ali.pk}": "08:00"})
        rec = Attendance.objects.get(employee=self.ali, date=self.day)
        self.assertEqual((rec.status, rec.check_in), ("absent", None))

    def test_a_future_date_posted_by_hand_is_refused(self):
        resp = self.post("confirm", day=self.today + timedelta(days=2), **{f"s_{self.ali.pk}": "present"})
        self.assertEqual(Attendance.objects.count(), 0)                         # not today's either
        self.assertEqual(resp.context["d"], self.today)
        self.assertContains(resp, "Nothing was saved")

    def test_the_fill_choice_must_be_one_the_sheet_offers(self):
        resp = self.post("review", fill="paid_leave")
        self.assertContains(resp, "Choose Present, Absent or Half day")
        self.assertEqual(Attendance.objects.count(), 0)

    def test_the_sheet_only_reaches_companies_the_person_may_see(self):
        other = Company.objects.create(code="C2", name="Company Two")
        stranger = Employee.objects.create(company=other, employee_no="X1", first_name="Zed",
                                           joining_date=date(2020, 1, 1))
        self.post("confirm", **{f"s_{stranger.pk}": "present"})
        self.assertEqual(Attendance.objects.filter(employee=stranger).count(), 0)
