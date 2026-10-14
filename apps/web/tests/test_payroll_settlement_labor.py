from datetime import date
from decimal import Decimal

from apps.core import approvals
from apps.leave.models import LeaveLedger
from apps.payroll import settlements
from apps.payroll.finalsettlement import FinalSettlement
from apps.payroll.test_labor_runs import WORKING, LaborCase
from apps.payroll.test_settlements import approval_of
from apps.web.tests.test_payroll_settlements import SettlementPageCase

D = Decimal


class LaborSettlementPageTests(SettlementPageCase, LaborCase):
    def ravi(self):
        emp = self.worker("Ravi", basis="daily", rate="5", ot=False)
        for year in (2025, 2026):
            LeaveLedger.objects.create(employee=emp, leave_type=self.annual, year=year, kind="entitlement", days=D("30"))
        self.attend(emp, WORKING)
        return emp

    def test_the_form_offers_staff_and_direct_labor_but_not_contractor_supplied_or_profileless_workers(self):
        ravi = self.ravi()
        contracted = self.worker("Contracted", basis="daily", rate="5", engagement="contracted", pay=None)
        from apps.employees.models import Employee
        orphan = Employee.objects.create(company=self.co, employee_no="L-900", first_name="Orphan", worker_type="labor",
                                         joining_date=date(2020, 1, 1))
        offered = set(self.client.get(self.url("payroll_settlement_create")).context["form"]
                      .fields["employee"].queryset.values_list("pk", flat=True))
        self.assertIn(ravi.pk, offered)
        self.assertIn(self.ali.pk, offered)
        self.assertNotIn(contracted.pk, offered)
        self.assertIn(orphan.pk, offered)                       # offered, then refused with a clear message below
        r = self.client.post(self.url("payroll_settlement_create"), self.data(employee=orphan.pk))
        self.assertContains(r, "no labor profile")

    def test_a_labor_settlement_from_the_pages(self):
        ravi = self.ravi()
        r = self.client.post(self.url("payroll_settlement_create"), self.data(employee=ravi.pk))
        case = FinalSettlement.objects.get(employee=ravi)
        self.assertRedirects(r, self.url("payroll_settlement", case.pk), fetch_redirect_response=False)
        page = self.client.get(r.url)
        for text in ("Ravi", "Wages 01 Mar to 31 Mar 2026", "135.000", "Monthly wage from the pay terms: daily rate 5.000 x 30 days",
                     "Annual leave: 37.5 days at 5.000", "Gratuity for service before 1 March 2024"):
            self.assertContains(page, text)
        settlements.submit_settlement(case, self.hr)
        approvals.decide(approval_of(case).pk, self.boss, "approve")
        html = self.client.get(self.url("payroll_settlement_certificate", case.pk)).content.decode()
        for text in ("END OF SERVICE CERTIFICATE", "Ravi", "31 March 2026", "BHD 150.000"):
            self.assertIn(text, html)
        statement = self.client.get(self.url("payroll_settlement_statement", case.pk)).content.decode()
        self.assertIn("Final settlement statement", statement)
