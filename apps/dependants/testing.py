"""Shared set-up for the family visa tests: a reporting manager who can approve, and a second HR user, because
nobody can approve a request they raised themselves."""
from datetime import timedelta

from django.contrib.auth.models import Group

from apps.accounts.models import User
from apps.compliance.models import Document, DocumentType
from apps.compliance.testing import ComplianceCase
from apps.core import approvals
from apps.core.models import ApprovalRequest
from apps.employees.models import Employee
from apps.organization.models import CompanyAccess

from . import eligibility
from . import services as family
from . import visa_services as visa
from .defaults import FLOW_CODE


class VisaCase(ComplianceCase):
    def setUp(self):
        super().setUp()
        boss_employee = Employee.objects.create(company=self.co, employee_no="IV-0009", first_name="Boss",
                                                last_name="Test", user=self.boss,
                                                joining_date=self.today - timedelta(days=900))
        Employee.objects.filter(pk=self.emp.pk).update(reporting_manager=boss_employee)
        self.emp.refresh_from_db()
        self.hr_b = User.objects.create_user("hr_b", email="hr_b@example.com")
        self.hr_b.groups.add(Group.objects.get(name="HR"))
        CompanyAccess.objects.create(user=self.hr_b, company=self.co)
        self.visa_type = DocumentType.objects.get(code="family-visa")
        self.dpassport = DocumentType.objects.get(code="dependant-passport")
        # nobody has the family visa option until it is decided, so the older tests allow their sponsors
        for sponsor in (self.emp, getattr(self, "emp2", None)):
            if sponsor is not None:
                eligibility.set_exception(sponsor, "allowed", "set-up for the tests")
        self.kid = self.person("Yusuf")

    def iso(self, days):
        return (self.today + timedelta(days=days)).isoformat()

    def person(self, name, employee=None, relationship="child"):
        extra = {"date_of_birth": self.today - timedelta(days=2200)} if relationship == "child" else {}
        return family.add_dependant(self.hr, employee or self.emp, relationship=relationship, first_name=name, **extra)

    def make_visa(self, dependant=None, days=300, number=""):
        return Document.objects.create(dependant=dependant or self.kid, document_type=self.visa_type, number=number,
                                       expiry_date=self.today + timedelta(days=days), responsible=self.pro)

    def ask(self, kind=visa.Kind.NEW, dependant=None, **kwargs):
        return visa.request_visa(dependant or self.kid, kind, requested_by=self.hr, **kwargs)

    def approval_of(self, req):
        return ApprovalRequest.objects.get(object_id=str(req.pk), flow__code=FLOW_CODE)

    def approve(self, req):
        """Both steps: the reporting manager, then a different HR user."""
        a = self.approval_of(req)
        approvals.decide(a.pk, self.boss, "approve")
        approvals.decide(a.pk, self.hr_b, "approve")
        req.refresh_from_db()
        return req
