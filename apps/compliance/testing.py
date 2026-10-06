import tempfile
from datetime import timedelta

from django.contrib.auth.models import Group
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.accounts.models import User
from apps.employees.models import Employee
from apps.organization.models import Company, CompanyAccess

from .models import Document, DocumentType


class ComplianceCase(TestCase):
    """Shared fixture: two companies, one user per role, and files kept in a throw-away folder."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        override = override_settings(PRIVATE_MEDIA_ROOT=tmp.name)
        override.enable()
        self.addCleanup(override.disable)

        self.today = timezone.localdate()
        self.co = Company.objects.create(code="IV", name="IV Spare Parts")
        self.other_co = Company.objects.create(code="X2", name="Second Co")

        def make_user(name, group=None, company=None, email=True):
            user = User.objects.create_user(name, email=f"{name}@example.com" if email else "")
            if group:
                user.groups.add(Group.objects.get(name=group))
            if company:
                CompanyAccess.objects.create(user=user, company=company)
            return user

        self.hr = make_user("hr", "HR", self.co)
        self.finance = make_user("fin", "Finance", self.co)
        self.boss = make_user("boss", "Management", self.co)
        self.pro = make_user("pro", company=self.co)             # responsible person, in no alert group
        self.other_hr = make_user("hr2", "HR", self.other_co)
        self.nobody = make_user("nobody")                        # no permissions at all
        self.cr = DocumentType.objects.get(code="cr")
        self.passport = DocumentType.objects.get(code="passport")
        self.residence = DocumentType.objects.get(code="residence-permit")

        def make_employee(company, no, first, **kwargs):
            return Employee.objects.create(company=company, employee_no=no, first_name=first, last_name="Test",
                                           joining_date=self.today - timedelta(days=400), **kwargs)

        self.emp = make_employee(self.co, "IV-0001", "Ali", nationality="Indian")
        self.emp2 = make_employee(self.co, "IV-0002", "Sara", nationality="Bahraini")
        self.emp_other = make_employee(self.other_co, "X2-0001", "Zed")

    def make_employee_doc(self, employee=None, days=25, *, dtype=None, **kwargs):
        employee = employee or self.emp
        kwargs.setdefault("responsible", self.pro)
        return Document.objects.create(employee=employee, company=employee.company,
                                       document_type=dtype or self.residence,
                                       expiry_date=self.today + timedelta(days=days), **kwargs)

    def make_doc(self, days=25, *, dtype=None, company=None, **kwargs):
        kwargs.setdefault("responsible", self.pro)
        return Document.objects.create(company=company or self.co, document_type=dtype or self.cr,
                                       expiry_date=self.today + timedelta(days=days), **kwargs)
