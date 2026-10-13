"""Shared set-up for the air ticket tests."""
from datetime import timedelta

from apps.compliance.testing import ComplianceCase
from apps.employees.models import Employee
from apps.organization.models import Grade

from .models import TicketPolicy


class AirCase(ComplianceCase):
    def setUp(self):
        super().setUp()
        self.officer = Grade.objects.create(code="G2", name="Officer", rank=2)
        self.manager = Grade.objects.create(code="G7", name="Manager", rank=7)
        self.set_emp(self.emp, nationality="Indian", grade=self.officer, employment_type="permanent",
                     joining_date=self.today - timedelta(days=400))
        self.set_emp(self.emp2, nationality="Bahraini")

    def set_emp(self, emp, **fields):
        Employee.objects.filter(pk=emp.pk).update(**fields)
        emp.refresh_from_db()
        return emp

    def policy(self, **over):
        data = dict(company=self.co, name="Expat staff", nationalities=["Indian"], interval_months=12,
                    first_after_months=12, spouse_covered=True, max_children=2, mode="company")
        data.update(over)
        return TicketPolicy.objects.create(**data)
