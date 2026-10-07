import csv
import io
from datetime import timedelta
from decimal import Decimal as D

from django.urls import reverse

from apps.compliance import services as compliance_services
from apps.compliance.models import Document, DocumentType
from apps.compliance.testing import ComplianceCase
from apps.vehicles import accidents, financing, fines, fuel, odometer
from apps.vehicles.incidents import Accident, Fine
from apps.vehicles.models import Vehicle
from apps.vehicles.upkeep import ServicePlan


class FleetPageCase(ComplianceCase):
    def setUp(self):
        super().setUp()
        self.v = Vehicle.objects.create(company=self.co, plate_number="123456", make="Toyota", model="Hilux",
                                        year=2022)
        ServicePlan.objects.create(vehicle=self.v, name="Oil change", every_km=5000,
                                   baseline_on=self.today - timedelta(days=10), baseline_km=0)
        odometer.record_odometer(self.v, 5100, self.today)                                  # the oil change is overdue
        dtype = DocumentType.objects.get_or_create(
            code="vehicle-registration", defaults=dict(name="Vehicle Registration", applies_to="vehicle"))[0]
        compliance_services.create_document(
            self.hr, Document(document_type=dtype, vehicle=self.v, expiry_date=self.today + timedelta(days=10)))
        fines.save_fine(Fine(vehicle=self.v, fined_on=self.today, offence="Speeding", amount=D("20")))
        accidents.save_accident(Accident(vehicle=self.v, occurred_on=self.today, description="Scrape"))
        financing.create_loan(self.v, lender="Bank of Bahrain", financed_amount=D("5000"), installment_count=12,
                              installment_amount=D("450"), first_due_on=self.today - timedelta(days=40))
        fuel.add_fill(self.v, filled_on=self.today, litres=D("30"), cost=D("12"), km=5200)


class DashboardPageTests(FleetPageCase):
    def test_each_role_sees_its_own_dashboard(self):
        page = reverse("web:vehicle_dashboard")
        self.assertEqual(self.client.get(page).status_code, 302)                          # sign in first
        self.client.force_login(self.nobody)
        self.assertEqual(self.client.get(page).status_code, 403)

        self.client.force_login(self.finance)
        r = self.client.get(page)
        for text in ("Services due", "Unpaid fines", "Open accidents", "Overdue installments",
                     "Running cost this month", "Last 12 months", "123456", "Oil change"):
            self.assertContains(r, text)

        self.client.force_login(self.hr)                                                   # HR runs the fleet, not the money
        r = self.client.get(page)
        for text in ("Services due", "Unpaid fines", "123456"):
            self.assertContains(r, text)
        for text in ("Overdue installments", "Running cost this month", "Last 12 months", "Bank of Bahrain"):
            self.assertNotContains(r, text)

        self.client.force_login(self.boss)
        self.assertContains(self.client.get(page), "Running cost this month")
        self.client.force_login(self.other_hr)                                             # someone else's company
        self.assertNotContains(self.client.get(page), "123456")


class CostPageTests(FleetPageCase):
    def setUp(self):
        super().setUp()
        self.page = reverse("web:vehicle_costs")

    def test_only_finance_and_management_see_costs(self):
        for user, expected in ((self.nobody, 403), (self.hr, 403), (self.other_hr, 403), (self.finance, 200),
                               (self.boss, 200)):
            self.client.force_login(user)
            self.assertEqual(self.client.get(self.page).status_code, expected, user.username)

    def test_the_report_and_its_filters(self):
        self.client.force_login(self.finance)
        r = self.client.get(self.page)
        self.assertContains(r, "123456")
        self.assertContains(r, "32.000")                                                    # today's fuel 12 and fine 20
        other_month = self.today.month % 12 + 1
        r = self.client.get(self.page, {"year": self.today.year, "month": other_month})
        self.assertContains(r, "Nothing was recorded")
        self.assertNotContains(r, "123456")

    def test_bad_filters_do_not_crash_the_page(self):
        self.client.force_login(self.finance)
        for params in ({"year": "abc"}, {"month": "99"}, {"year": "1900"}, {"company": "zzz"},
                       {"company": str(self.other_co.pk)}):
            r = self.client.get(self.page, params)
            self.assertEqual(r.status_code, 200, params)
            self.assertContains(r, "123456")                      # unusable values fall back to the defaults

    def test_the_csv_download(self):
        self.client.force_login(self.finance)
        r = self.client.get(self.page, {"export": "csv"})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r["Content-Type"].startswith("text/csv"))
        self.assertIn(f"vehicle-costs-{self.today.year}.csv", r["Content-Disposition"])
        rows = list(csv.reader(io.StringIO(r.content.decode("utf-8-sig"))))
        self.assertEqual(rows[0][:4], ["Plate", "Vehicle", "Company", "Fuel"])
        self.assertEqual(rows[1][0], "123456")
        self.assertEqual((rows[1][3], rows[1][5]), ("12.000", "20.000"))                   # fuel, fines
        self.assertEqual(rows[-1][0], "Total")
        self.client.force_login(self.hr)
        self.assertEqual(self.client.get(self.page, {"export": "csv"}).status_code, 403)

    def test_a_formula_typed_into_a_vehicle_cannot_run_in_a_spreadsheet(self):
        evil = Vehicle.objects.create(company=self.co, plate_number="999", make='=HYPERLINK("http://example.com")')
        fuel.add_fill(evil, filled_on=self.today, litres=D("10"), cost=D("5"), km=10)
        self.client.force_login(self.finance)
        content = self.client.get(self.page, {"export": "csv"}).content.decode("utf-8-sig")
        row = next(r for r in csv.reader(io.StringIO(content)) if r[0] == "999")
        self.assertTrue(row[1].startswith("'="), row[1])


class VehiclePagesWithMoneyTests(FleetPageCase):
    """The vehicle page and the list, which now carry the cost card and the dashboard and cost buttons."""

    def test_the_cost_card_is_for_finance_and_management_only(self):
        page = reverse("web:vehicle_detail", args=[self.v.pk])
        for user in (self.finance, self.boss):
            self.client.force_login(user)
            r = self.client.get(page)
            self.assertContains(r, f"Cost in {self.today.year}")
            self.assertContains(r, "running cost")
            self.assertContains(r, "32.000")                              # today's fuel (12) and fine (20)
        self.client.force_login(self.hr)
        r = self.client.get(page)
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, "Cost in ")

    def test_the_list_links_to_the_dashboard_and_costs(self):
        listing = reverse("web:vehicle_list")
        self.client.force_login(self.hr)
        r = self.client.get(listing)
        self.assertContains(r, reverse("web:vehicle_dashboard"))
        self.assertNotContains(r, reverse("web:vehicle_costs"))
        self.client.force_login(self.finance)
        self.assertContains(self.client.get(listing), reverse("web:vehicle_costs"))
