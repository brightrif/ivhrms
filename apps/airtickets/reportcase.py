"""Shared set-up for the air ticket REPORT tests: tickets already taken, in two people, two years, two departments."""
from datetime import date, timedelta
from decimal import Decimal

from apps.organization.models import Department

from .models import TicketEntitlement
from .requestcase import RequestCase


class ReportCase(RequestCase):
    def setUp(self):
        super().setUp()
        self.year = self.today.year
        self.ops = Department.objects.create(company=self.co, code="OPS", name="Operations")
        self.set_emp(self.emp, department=self.ops)
        self.set_emp(self.emp2, nationality="bahrain")             # written loosely, reported as Bahraini
        self.everyone = self.policy(name="Everyone", nationalities=[])

    def taken(self, emp, on, cost=None, status="used", destination=""):
        """A closed ticket, written straight to the table so a test can place it in any year."""
        cycle = 100 + TicketEntitlement.objects.count()
        return TicketEntitlement.objects.create(
            company=self.co, employee=emp, cycle=cycle, status=status, eligible_from=on - timedelta(days=365),
            mode="company", used_on=on, cost=cost, destination=destination)

    def standard_year(self):
        """Ali: 200.000 and one without a cost this year, 150.000 last year. Sara: 100.500 allowance this year."""
        jan = date(self.year, 1, 1)
        self.taken(self.emp, jan, Decimal("200.000"), destination="Cochin (COK)")
        self.taken(self.emp, jan, None)
        self.taken(self.emp, date(self.year - 1, 6, 15), Decimal("150.000"))
        self.taken(self.emp2, jan, Decimal("100.500"), status="paid")
