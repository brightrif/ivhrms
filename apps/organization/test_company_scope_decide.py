from datetime import date

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import RequestFactory, TestCase

from apps.audit.context import reset_request, set_request
from apps.core import approvals
from apps.core.models import ApprovalFlow, ApprovalRequest
from apps.employees.models import Employee
from apps.organization.models import Company

User = get_user_model()


class DecideInTheOtherCompanyTests(TestCase):
    def setUp(self):
        self.spare = Company.objects.create(code="ISP", name="IV Spare Parts")
        self.build = Company.objects.create(code="IVC", name="IV Construction")
        self.root = User.objects.create_user("root")
        self.root.is_superuser = True
        self.root.save()
        flow = ApprovalFlow.objects.create(code="leave", name="Leave")
        employee = Employee.objects.create(company=self.build, employee_no="C-1", first_name="C-1",
                                           joining_date=date(2020, 1, 1))
        self.ask = ApprovalRequest.objects.create(
            company=self.build, flow=flow, employee=employee, requested_by=self.root,
            content_type=ContentType.objects.get_for_model(Company), object_id=str(self.build.pk))

    def test_a_request_of_the_other_company_cannot_be_decided_from_this_one(self):
        request = RequestFactory().get("/")
        request.user = self.root
        request.company_scope = self.spare.pk
        token = set_request(request)
        try:
            with self.assertRaisesMessage(approvals.ApprovalError, "other company"):
                approvals.decide(self.ask.pk, self.root, "approve")
        finally:
            reset_request(token)
        self.ask.refresh_from_db()
        self.assertEqual(self.ask.status, "pending")
