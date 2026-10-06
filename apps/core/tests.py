from datetime import date

from django.contrib.auth.models import Group
from django.test import TestCase

from apps.accounts.models import User
from apps.audit.models import AuditEvent
from apps.employees.models import Employee
from apps.organization.models import Company, CompanyAccess
from . import approvals
from .models import ApprovalFlow, ApprovalStep


class ApprovalTests(TestCase):
    def setUp(self):
        self.co = Company.objects.create(code="C1", name="Company One")
        self.mgr_user = User.objects.create_user("mgr")
        self.hr_user = User.objects.create_user("hr")
        self.emp_user = User.objects.create_user("emp")

        # hr = Group.objects.create(name="HR")
        hr, _ = Group.objects.get_or_create(name="HR")
        self.hr_user.groups.add(hr)
        CompanyAccess.objects.create(user=self.hr_user, company=self.co)

        mgr = Employee.objects.create(
            company=self.co, employee_no="E1", first_name="M",
            user=self.mgr_user, joining_date=date(2020, 1, 1))
        self.emp = Employee.objects.create(
            company=self.co, employee_no="E2", first_name="E",
            user=self.emp_user, reporting_manager=mgr, joining_date=date(2021, 1, 1))

        flow = ApprovalFlow.objects.create(code="test.flow", name="Test")
        ApprovalStep.objects.create(flow=flow, order=1, name="Manager", approver_type="manager")
        ApprovalStep.objects.create(flow=flow, order=2, name="HR", approver_type="group", group=hr)

    def test_two_step_flow(self):
        req = approvals.submit("test.flow", self.emp, employee=self.emp, requested_by=self.emp_user)
        with self.assertRaises(approvals.ApprovalError):       # HR cannot skip the manager step
            approvals.decide(req.pk, self.hr_user, "approve")
        with self.assertRaises(approvals.ApprovalError):       # nobody approves their own request
            approvals.decide(req.pk, self.emp_user, "approve")
        approvals.decide(req.pk, self.mgr_user, "approve")
        req = approvals.decide(req.pk, self.hr_user, "approve")
        self.assertEqual(req.status, "approved")
        self.assertTrue(AuditEvent.objects.filter(module="employees", company_id=self.co.pk).exists())

    def test_reject_needs_comment(self):
        req = approvals.submit("test.flow", self.emp, employee=self.emp, requested_by=self.emp_user)
        with self.assertRaises(approvals.ApprovalError):
            approvals.decide(req.pk, self.mgr_user, "reject")
        req = approvals.decide(req.pk, self.mgr_user, "reject", comment="No cover")
        self.assertEqual(req.status, "rejected")