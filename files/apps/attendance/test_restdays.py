from datetime import date, datetime, time, timedelta

from django.test import TestCase
from django.utils import timezone

from apps.employees.models import Employee
from apps.organization.models import Company

from . import restdays, services
from .models import Attendance


class RestDayCase(TestCase):
    def setUp(self):
        self.co = Company.objects.create(code="C1", name="Company One")
        self.emp = Employee.objects.create(company=self.co, employee_no="E1", first_name="Ali",
                                           joining_date=date(2020, 1, 1))
        self.day = timezone.localdate() - timedelta(days=3)

    def at(self, hour, minute=0):
        return timezone.make_aware(datetime.combine(self.day, time(hour, minute)))

    def rec(self):
        return Attendance.objects.get(employee=self.emp, date=self.day)


class RecordTests(RestDayCase):
    def test_a_rest_day_is_recorded_by_the_system(self):
        row = restdays.record_rest_day(self.emp, self.day, "weekly_off")
        self.assertEqual((row.status, row.source), ("weekly_off", "system"))
        self.assertIsNone(restdays.record_rest_day(self.emp, self.day, "holiday"))      # the day already has one

    def test_a_holiday_is_recorded_as_a_holiday(self):
        self.assertEqual(restdays.record_rest_day(self.emp, self.day, "holiday").status, "holiday")

    def test_a_day_that_already_has_an_entry_is_left_alone(self):
        services.mark_attendance(self.emp, self.day, "present")
        self.assertIsNone(restdays.record_rest_day(self.emp, self.day, "weekly_off"))
        self.assertEqual(self.rec().status, "present")

    def test_recording_twice_makes_one_row(self):
        restdays.record_rest_day(self.emp, self.day, "weekly_off")
        self.assertIsNone(restdays.record_rest_day(self.emp, self.day, "weekly_off"))
        self.assertEqual(Attendance.objects.filter(employee=self.emp).count(), 1)


class ReplaceTests(RestDayCase):
    def test_a_worked_rest_day_becomes_present_with_its_times(self):
        restdays.record_rest_day(self.emp, self.day, "weekly_off")
        restdays.replace_rest_day(self.emp, self.day, "present", check_in=self.at(8), check_out=self.at(17))
        rec = self.rec()
        self.assertEqual((rec.status, rec.source), ("present", "manual"))
        self.assertIn("rest day", rec.remarks)
        self.assertEqual(timezone.localtime(rec.check_out).strftime("%H:%M"), "17:00")
        self.assertEqual(Attendance.objects.filter(employee=self.emp).count(), 1)             # replaced, not added

    def test_a_holiday_can_be_worked_too_and_half_a_day_counts(self):
        restdays.record_rest_day(self.emp, self.day, "holiday")
        restdays.replace_rest_day(self.emp, self.day, "half_day")
        self.assertEqual(self.rec().status, "half_day")

    def test_only_the_systems_rest_day_rows_can_be_replaced(self):
        services.mark_attendance(self.emp, self.day, "absent")
        with self.assertRaisesMessage(services.AttendanceError, "Use a correction"):
            restdays.replace_rest_day(self.emp, self.day, "present")
        self.assertEqual(self.rec().status, "absent")

    def test_a_day_with_nothing_recorded_is_not_a_replacement(self):
        with self.assertRaisesMessage(services.AttendanceError, "Nothing is recorded"):
            restdays.replace_rest_day(self.emp, self.day, "present")

    def test_a_closed_payroll_period_and_a_future_day_are_refused(self):
        restdays.record_rest_day(self.emp, self.day, "weekly_off")
        Attendance.objects.filter(employee=self.emp).update(is_locked=True)
        with self.assertRaisesMessage(services.AttendanceError, "closed payroll period"):
            restdays.replace_rest_day(self.emp, self.day, "present")
        tomorrow = timezone.localdate() + timedelta(days=1)
        Attendance.objects.create(employee=self.emp, date=tomorrow, status="weekly_off", source="system",
                                  **services._derive(self.emp, tomorrow, "weekly_off", None, None))
        with self.assertRaisesMessage(services.AttendanceError, "future"):
            restdays.replace_rest_day(self.emp, tomorrow, "present")

    def test_check_out_must_be_after_check_in(self):
        restdays.record_rest_day(self.emp, self.day, "weekly_off")
        with self.assertRaisesMessage(services.AttendanceError, "Check-out must be after check-in"):
            restdays.replace_rest_day(self.emp, self.day, "present", check_in=self.at(17), check_out=self.at(8))
        self.assertEqual(self.rec().status, "weekly_off")
