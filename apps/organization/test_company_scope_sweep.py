from datetime import date
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import RequestFactory, TestCase
from django.urls import reverse

from apps.audit.context import reset_request, set_request
from apps.core import approvals
from apps.core.models import ApprovalFlow, ApprovalRequest
from apps.employees.models import Employee
from apps.organization import scope
from apps.organization.models import Company, CompanyAccess, Department
from apps.organization.services import all_manageable_companies, manageable_companies

User = get_user_model()


class SweepCase(TestCase):
    def setUp(self):
        self.spare = Company.objects.create(code="ISP", name="IV Spare Parts", is_default=True)
        self.build = Company.objects.create(code="IVC", name="IV Construction")
        self.root = User.objects.create_user("root")
        self.root.is_superuser = True
        self.root.is_staff = True
        self.root.save()

    def working_in(self, user, company):
        """Run code as `user` working in `company`, the way the middleware sets a request up."""
        request = RequestFactory().get("/")
        request.user = user
        request.company_scope = company.pk
        return request


class ManageableCompaniesTests(SweepCase):
    def test_the_separate_view_narrows_the_companies_a_person_may_manage(self):
        token = set_request(self.working_in(self.root, self.build))
        try:
            self.assertEqual(list(manageable_companies(self.root)), [self.build])
            self.assertEqual(set(all_manageable_companies(self.root)), {self.spare, self.build})
        finally:
            reset_request(token)
        self.assertEqual(set(manageable_companies(self.root)), {self.spare, self.build})   # outside a request: all

    def test_the_organization_pages_show_only_the_company_being_worked_in(self):
        Department.objects.create(company=self.spare, code="HR", name="Spare HR")
        Department.objects.create(company=self.build, code="SITE", name="Construction Site Office")
        scope.set_view(scope.SEPARATE)
        self.client.force_login(self.root)

        page = self.client.get(reverse("web:company_list"))
        self.assertContains(page, reverse("web:company_edit", args=[self.spare.pk]))
        self.assertNotContains(page, reverse("web:company_edit", args=[self.build.pk]))

        page = self.client.get(reverse("web:department_list"))
        self.assertContains(page, "Spare HR")
        self.assertNotContains(page, "Construction Site Office")

        self.client.post(reverse("web:company_switch"), {"company": self.build.pk})
        page = self.client.get(reverse("web:company_list"))
        self.assertContains(page, reverse("web:company_edit", args=[self.build.pk]))
        self.assertNotContains(page, reverse("web:company_edit", args=[self.spare.pk]))
        page = self.client.get(reverse("web:department_list"))
        self.assertContains(page, "Construction Site Office")
        self.assertNotContains(page, "Spare HR")

    def test_in_the_combined_view_both_companies_still_show(self):
        self.client.force_login(self.root)
        page = self.client.get(reverse("web:company_list"))
        self.assertContains(page, "IV Spare Parts")
        self.assertContains(page, "IV Construction")

    def test_the_colour_table_still_lists_both_companies_in_the_separate_view(self):
        scope.set_view(scope.SEPARATE)
        self.client.force_login(self.root)
        page = self.client.get(reverse("web:settings_module", args=["organization"]))
        self.assertContains(page, "IV Spare Parts")
        self.assertContains(page, "IV Construction")
        r = self.client.post(reverse("web:company_colours_set", args=[self.build.pk]), {"theme_color": "#112233"})
        self.assertNotEqual(r.status_code, 404)


class ApprovalsTests(SweepCase):
    def setUp(self):
        super().setUp()
        self.flow = ApprovalFlow.objects.create(code="leave", name="Leave")
        self.ask_spare = self.request_in(self.spare, "S-1")
        self.ask_build = self.request_in(self.build, "C-1")

    def request_in(self, company, number):
        employee = Employee.objects.create(company=company, employee_no=number, first_name=number,
                                           joining_date=date(2020, 1, 1))
        return ApprovalRequest.objects.create(
            company=company, flow=self.flow, employee=employee, requested_by=self.root,
            content_type=ContentType.objects.get_for_model(Company), object_id=str(company.pk))

    def pending(self, user):
        with mock.patch("apps.core.approvals.can_act", return_value=True):
            return {r.pk for r in approvals.pending_for(user)}

    def test_combined_view_shows_waiting_requests_of_every_company(self):
        self.assertEqual(self.pending(self.root), {self.ask_spare.pk, self.ask_build.pk})

    def test_separate_view_shows_only_the_company_being_worked_in(self):
        token = set_request(self.working_in(self.root, self.build))
        try:
            self.assertEqual(self.pending(self.root), {self.ask_build.pk})
        finally:
            reset_request(token)
