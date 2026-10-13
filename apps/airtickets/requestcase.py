"""Shared set-up for the air ticket REQUEST tests: a reporting manager who can approve, a second HR user (nobody
approves a request they raised), a policy, and a family."""
from datetime import timedelta

from django.contrib.auth.models import Group

from apps.accounts.models import User
from apps.core import approvals
from apps.core.models import ApprovalRequest
from apps.dependants import services as family
from apps.employees.models import Employee
from apps.organization.models import CompanyAccess

from . import request_services as tickets
from . import services
from .defaults import FLOW_CODE
from .testing import AirCase


class RequestCase(AirCase):
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

        self.pol = self.policy()                                  # Indian staff: spouse and up to 2 children
        self.ent = services.sync_entitlement(self.emp)
        child = lambda name: family.add_dependant(self.hr, self.emp, relationship="child", first_name=name,
                                                  date_of_birth=self.today - timedelta(days=2200))
        self.noor = family.add_dependant(self.hr, self.emp, relationship="spouse", first_name="Noor")
        self.yusuf, self.layla, self.omar = child("Yusuf"), child("Layla"), child("Omar")
        self.mona = family.add_dependant(self.hr, self.emp, relationship="parent", first_name="Mona")

    def iso(self, days):
        return (self.today + timedelta(days=days)).isoformat()

    def ask(self, **over):
        data = dict(depart_on=self.today + timedelta(days=30), return_on=self.today + timedelta(days=60),
                    destination="Cochin (COK)", dependants=[], notes="", requested_by=self.hr)
        data.update(over)
        return tickets.request_ticket(self.emp, **data)

    def approval_of(self, req):
        return ApprovalRequest.objects.get(object_id=str(req.pk), flow__code=FLOW_CODE)

    def approve(self, req):
        """Both steps: the reporting manager, then an HR user who is not the one who asked."""
        a = self.approval_of(req)
        approvals.decide(a.pk, self.boss, "approve")
        approvals.decide(a.pk, self.hr_b, "approve")
        req.refresh_from_db()
        return req

    def to_allowance(self, amount="100"):
        self.pol.mode, self.pol.allowance_amount = "allowance", amount
        self.pol.save()
        services.sync_entitlement(self.emp)
