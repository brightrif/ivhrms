from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.contrib.auth.models import Group
from django.contrib.contenttypes.models import ContentType
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.audit.models import AuditEvent
from apps.core import approvals
from apps.core.models import ApprovalRequest
from apps.employees.models import Employee
from apps.leave import services as leave_services
from apps.leave.models import LeaveLedger, LeaveType
from apps.organization.models import Company, CompanyAccess
from apps.scheduling import services as sched
from apps.scheduling.models import Holiday, Shift

from . import services
from .models import Attendance


class BaseCase(TestCase):
    def setUp(self):
        self.co = Company.objects.create(code="C1", name="Company One")
        call_command("seed_phase2", verbosity=0)
        self.mgr_user = User.objects.create_user("mgr")
        self.hr_user = User.objects.create_user("hr")
        self.emp_user = User.objects.create_user("emp")
        self.hr_user.groups.add(Group.objects.get(name="HR"))
        CompanyAccess.objects.create(user=self.hr_user, company=self.co)
        self.mgr = Employee.objects.create(company=self.co, employee_no="E1", first_name="Mona",
                                           user=self.mgr_user, joining_date=date(2020, 1, 1))
        self.emp = Employee.objects.create(company=self.co, employee_no="E2", first_name="Eli",
                                           user=self.emp_user, reporting_manager=self.mgr,
                                           joining_date=date(2021, 1, 1))
        self.annual = LeaveType.objects.get(company=self.co, code="ANNUAL")

        # A Sunday ~2 weeks ahead whose Sun..Sat week stays inside one calendar year
        start = timezone.localdate() + timedelta(days=14)
        start += timedelta(days=(6 - start.weekday()) % 7)
        while (start + timedelta(days=6)).year != start.year:
            start += timedelta(days=7)
        self.start = start
        leave_services.grant_entitlement(self.emp, self.annual, start.year)

    def approval_for(self, obj):
        ct = ContentType.objects.get_for_model(obj)
        return ApprovalRequest.objects.get(content_type=ct, object_id=str(obj.pk))

    def approve_fully(self, approval):
        approvals.decide(approval.pk, self.mgr_user, "approve")
        approvals.decide(approval.pk, self.hr_user, "approve")


class LeaveTests(BaseCase):
    def setUp(self):
        super().setUp()
        Holiday.objects.create(company=self.co, date=self.start + timedelta(days=2), name="Test holiday")

    def apply(self, a, b, **kw):
        return leave_services.apply(self.emp, self.annual, self.start + timedelta(days=a),
                                    self.start + timedelta(days=b), requested_by=self.emp_user, **kw)

    def test_holiday_not_charged_and_pending_reserves_balance(self):
        req = self.apply(0, 4)                    # Sun-Thu, Tuesday is a holiday
        self.assertEqual(req.days, Decimal("4"))
        self.assertEqual(leave_services.available(self.emp, self.annual, self.start.year), Decimal("26"))
        self.assertEqual(leave_services.balance(self.emp, self.annual, self.start.year), Decimal("30"))

    def test_approval_updates_balance_and_attendance_then_cancel_restores(self):
        req = self.apply(0, 4)
        self.approve_fully(self.approval_for(req))
        req.refresh_from_db()
        self.assertEqual(req.status, "approved")
        self.assertEqual(leave_services.balance(self.emp, self.annual, self.start.year), Decimal("26"))
        days = set(Attendance.objects.filter(leave_request=req).values_list("date", flat=True))
        self.assertEqual(len(days), 4)
        self.assertNotIn(self.start + timedelta(days=2), days)

        leave_services.cancel(req.pk, self.emp_user)
        self.assertEqual(leave_services.balance(self.emp, self.annual, self.start.year), Decimal("30"))
        self.assertFalse(Attendance.objects.filter(leave_request=req).exists())

    def test_reject_changes_nothing(self):
        req = self.apply(0, 4)
        approvals.decide(self.approval_for(req).pk, self.mgr_user, "reject", comment="Busy period")
        req.refresh_from_db()
        self.assertEqual(req.status, "rejected")
        self.assertEqual(leave_services.balance(self.emp, self.annual, self.start.year), Decimal("30"))
        self.assertFalse(Attendance.objects.filter(leave_request=req).exists())

    def test_overlap_is_rejected(self):
        self.apply(0, 1)
        with self.assertRaises(leave_services.LeaveError):
            self.apply(1, 3)

    def test_insufficient_balance_counts_pending_requests(self):
        LeaveLedger.objects.create(employee=self.emp, leave_type=self.annual, year=self.start.year,
                                   kind="adjustment", days=Decimal("-28"))        # leaves 2 days
        self.apply(0, 1)                                                          # Sun, Mon = 2 days
        with self.assertRaises(leave_services.LeaveError):
            self.apply(3, 4)                                                      # no balance left


class AttendanceTests(BaseCase):
    def setUp(self):
        super().setUp()
        self.shift = Shift.objects.create(company=self.co, code="GEN", name="General",
                                          start_time=time(8, 0), end_time=time(17, 0), grace_minutes=10)
        sched.assign_shift(self.emp, self.shift, date(2021, 1, 1))
        self.day = timezone.localdate() - timedelta(days=3)

    def at(self, d, h, m):
        return timezone.make_aware(datetime.combine(d, time(h, m)))

    def test_late_entry_duplicate_and_future(self):
        rec = services.mark_attendance(self.emp, self.day, "present",
                                       check_in=self.at(self.day, 8, 25), check_out=self.at(self.day, 17, 0))
        self.assertEqual(rec.status, "late_entry")
        self.assertEqual(rec.late_minutes, 25)
        with self.assertRaises(services.AttendanceError):
            services.mark_attendance(self.emp, self.day, "present")
        with self.assertRaises(services.AttendanceError):
            services.mark_attendance(self.emp, timezone.localdate() + timedelta(days=1), "present")

    def test_correction_needs_approval_and_is_audited(self):
        rec = services.mark_attendance(self.emp, self.day, "absent")
        corr = services.request_correction(
            self.emp, self.day, "present", requested_by=self.emp_user, reason="Forgot to punch in",
            check_in=self.at(self.day, 8, 0), check_out=self.at(self.day, 17, 0))
        rec.refresh_from_db()
        self.assertEqual(rec.status, "absent")                      # unchanged until approved
        self.approve_fully(self.approval_for(corr))
        rec.refresh_from_db()
        self.assertEqual(rec.status, "present")
        self.assertEqual(rec.source, "correction")
        self.assertTrue(AuditEvent.objects.filter(module="attendance", action="update",
                                                  object_id=str(rec.pk)).exists())

    def test_fill_weekly_off_is_idempotent(self):
        today = timezone.localdate()
        friday = today - timedelta(days=((today.weekday() - 4) % 7) or 7)
        self.assertEqual(services.fill_non_working_days(self.co, friday), 2)
        self.assertEqual(Attendance.objects.filter(date=friday, status="weekly_off").count(), 2)
        self.assertEqual(services.fill_non_working_days(self.co, friday), 0)