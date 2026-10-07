from datetime import date, timedelta
from decimal import Decimal as D

from apps.compliance import services as compliance_services
from apps.compliance.models import Document, DocumentType, RenewalPayment
from apps.employees.models import Employee

from . import accidents, assignments, financing, fines, fuel, maintenance, odometer, reports
from .incidents import Accident, Fine
from .models import Vehicle
from .test_upkeep import UpkeepCase
from .upkeep import ServicePlan, ServiceRecord


def reg_type():
    return DocumentType.objects.get_or_create(
        code="vehicle-registration", defaults=dict(name="Vehicle Registration", applies_to="vehicle"))[0]


class CostReportTests(UpkeepCase):
    """One vehicle's year: dates are in last year so they are always in the past, whatever today is."""

    def setUp(self):
        super().setUp()
        self.Y = self.today.year - 1
        self.v = self.vehicle()

    def d(self, month, day=10):
        return date(self.Y, month, day)

    def build(self, v=None):
        v = v or self.v
        ali = self.person(f"Ali{v.pk}")
        assignments.assign_vehicle(v, ali, self.d(1), 900, licence_override=True)
        fuel.add_fill(v, filled_on=self.d(2, 5), litres=D("40"), cost=D("12.000"), km=1000)
        fuel.add_fill(v, filled_on=self.d(3, 5), litres=D("30"), cost=D("9.000"), km=1400)
        maintenance.add_service(v, serviced_on=self.d(3), km=1450, parts_cost=D("20"), labour_cost=D("5"))
        crash = accidents.save_accident(Accident(vehicle=v, occurred_on=self.d(4, 1), description="Rear-ended",
                                                 claim_status=Accident.Claim.PAID, insurance_recovered=D("80")))
        repair = maintenance.add_service(v, serviced_on=self.d(4, 2), km=1500, kind=ServiceRecord.Kind.REPAIR,
                                         parts_cost=D("100"), labour_cost=D("20"))
        accidents.link_repair(crash, repair)
        fines.save_fine(Fine(vehicle=v, fined_on=self.d(3, 20), offence="Speeding", amount=D("20"),
                             reference=f"T-{v.pk}-1", charged_to_employee=True))
        parking = fines.save_fine(Fine(vehicle=v, fined_on=self.d(5, 1), offence="Parking", amount=D("20"),
                                       reference=f"T-{v.pk}-2"))
        fines.pay_fine(parking, self.d(5, 2), D("18"))                       # paid for less: the paid amount counts
        doc = Document(document_type=reg_type(), vehicle=v, expiry_date=self.today + timedelta(days=100))
        compliance_services.create_document(self.hr, doc)
        task = compliance_services.open_renewal(doc)
        compliance_services.record_payment(task, RenewalPayment(paid_on=self.d(6, 1), government_fee=D("50"),
                                                                service_fee=D("10")))
        loan = financing.create_loan(v, lender="Bank of Bahrain", financed_amount=D("5000"), installment_count=12,
                                     installment_amount=D("450"), first_due_on=self.d(1, 15))
        financing.pay_installment(loan.installments.get(number=1), self.d(1, 15), D("450"))
        financing.pay_installment(loan.installments.get(number=2), self.d(2, 15), D("450"))

    def row(self, report, vehicle=None):
        return next(r for r in report["rows"] if r.vehicle == (vehicle or self.v))

    def test_a_whole_year(self):
        self.build()
        report = reports.cost_report(self.finance, self.Y)
        r = self.row(report)
        self.assertEqual((r.fuel, r.service, r.fines, r.renewals, r.insurance),
                         (D("21"), D("145"), D("38"), D("60"), D("-80")))        # the repair is counted once, under service
        self.assertEqual((r.running, r.loan, r.total, r.recoverable), (D("184"), D("900"), D("1084"), D("20")))
        self.assertEqual(r.km, 600)
        self.assertAlmostEqual(float(r.per_km), 184 / 600, places=6)
        t = report["totals"]
        self.assertEqual((t.running, t.loan, t.total, t.km), (D("184"), D("900"), D("1084"), 600))
        self.assertAlmostEqual(float(t.per_km), 184 / 600, places=6)

    def test_a_single_month(self):
        self.build()
        report = reports.cost_report(self.finance, self.Y, month=3)
        r = self.row(report)
        self.assertEqual((r.fuel, r.service, r.fines, r.running, r.loan), (D("9"), D("25"), D("20"), D("54"), D("0")))
        self.assertEqual(r.km, 450)                                            # 1,000 at the end of February to 1,450
        self.assertAlmostEqual(float(r.per_km), 54 / 450, places=6)
        self.assertIsNone(report["months"])

    def test_the_year_is_split_by_month(self):
        self.build()
        months = reports.cost_report(self.finance, self.Y)["months"]
        by = {m.month.month: m for m in months}
        self.assertEqual(len(months), 12)
        self.assertEqual((by[3].running, by[4].running, by[6].running), (D("54"), D("40"), D("60")))
        self.assertEqual((by[1].loan, by[2].loan, by[2].running), (D("450"), D("450"), D("12")))
        self.assertEqual(max(m.pct for m in months), 100)
        self.assertEqual(by[1].pct, int(100 * 450 / 462))

    def test_cancelled_entries_are_left_out(self):
        self.build()
        march = self.v.fuel_fills.get(filled_on=self.d(3, 5))
        fuel.void_fill(march, "entered twice")
        self.assertEqual(self.row(reports.cost_report(self.finance, self.Y)).fuel, D("12"))

    def test_a_loan_settled_early_counts_what_was_paid_to_settle_it(self):
        other = self.vehicle("2")
        loan = financing.create_loan(other, lender="Bank", financed_amount=D("5000"), installment_count=12,
                                     installment_amount=D("450"), first_due_on=self.d(1, 15))
        financing.settle_early(loan, self.d(7, 1), D("3000"))
        r = self.row(reports.cost_report(self.finance, self.Y), other)
        self.assertEqual((r.loan, r.running), (D("3000"), D("0")))

    def test_people_only_see_their_own_companies_vehicles(self):
        self.build()
        self.assertEqual(reports.cost_report(self.other_hr, self.Y)["rows"], [])
        self.assertEqual(reports.cost_report(self.finance, self.Y, company_id=self.other_co.pk)["rows"], [])
        foreign = Vehicle.objects.create(company=self.other_co, plate_number="9", make="Kia")
        fuel.add_fill(foreign, filled_on=self.d(2, 5), litres=D("10"), cost=D("500"), km=10)
        mine = reports.cost_report(self.finance, self.Y)
        self.assertNotIn(foreign, [r.vehicle for r in mine["rows"]])
        self.assertEqual(mine["totals"].fuel, D("21"))

    def test_one_vehicle_can_be_picked_out(self):
        self.build()
        other = self.vehicle("2")
        fuel.add_fill(other, filled_on=self.d(2, 5), litres=D("10"), cost=D("7"), km=10)
        report = reports.cost_report(self.finance, self.Y, vehicle_id=other.pk)
        self.assertEqual([r.vehicle for r in report["rows"]], [other])

    def test_no_distance_means_no_cost_per_km(self):
        fuel.add_fill(self.v, filled_on=self.d(2, 5), litres=D("10"), cost=D("7"), km=500)
        r = self.row(reports.cost_report(self.finance, self.Y))
        self.assertEqual((r.fuel, r.km, r.per_km), (D("7"), 0, None))             # one reading: nothing to measure from

    def test_years_offered(self):
        self.assertEqual(reports.available_years(self.finance, self.today), [self.today.year])


class DashboardTests(UpkeepCase):
    def setUp(self):
        super().setUp()
        self.v = self.vehicle()
        self.ali = self.person()
        ServicePlan.objects.create(vehicle=self.v, name="Oil change", every_km=5000, baseline_on=self.ago(10),
                                   baseline_km=0)
        odometer.record_odometer(self.v, 5100, self.today)                              # the oil change is overdue
        doc = Document(document_type=reg_type(), vehicle=self.v, expiry_date=self.today + timedelta(days=10))
        compliance_services.create_document(self.hr, doc)                                # expires soon
        fines.save_fine(Fine(vehicle=self.v, fined_on=self.today, offence="Speeding", amount=D("20")))
        accidents.save_accident(Accident(vehicle=self.v, occurred_on=self.ago(2), description="Scrape"))
        financing.create_loan(self.v, lender="Bank", financed_amount=D("5000"), installment_count=12,
                              installment_amount=D("450"), first_due_on=self.today - timedelta(days=40))
        fuel.add_fill(self.v, filled_on=self.today, litres=D("30"), cost=D("12"), km=5200)

    def test_finance_sees_everything(self):
        d = reports.dashboard(self.finance, self.today)
        self.assertEqual((d["vehicles_total"], d["counts"]["active"]), (1, 1))
        self.assertEqual((d["services_total"], d["services_overdue"]), (1, 1))
        self.assertEqual((d["documents_total"], d["documents_expired"]), (1, 0))
        self.assertEqual((d["fines_total"], d["fines_amount"], d["accidents_total"]), (1, D("20"), 1))
        self.assertEqual([l.vehicle for l in d["loans_late"]], [self.v])
        self.assertEqual((len(d["trend"]), d["cost_month"], d["cost_ytd"]),
                         (12, D("32"), D("32")))                                        # today's fuel and fine
        self.assertEqual(d["trend"][-1].month, self.today.replace(day=1))

    def test_hr_sees_the_fleet_but_not_the_money(self):
        d = reports.dashboard(self.hr, self.today)
        self.assertFalse(d["can"]["finance"])
        for key in ("loans", "loans_late", "trend", "cost_month", "cost_ytd"):
            self.assertNotIn(key, d)
        self.assertEqual((d["services_total"], d["fines_total"], d["accidents_total"], d["documents_total"]),
                         (1, 1, 1, 1))

    def test_people_without_fleet_permissions_see_no_sections(self):
        d = reports.dashboard(self.nobody, self.today)
        self.assertFalse(any(d["can"].values()))
        for key in ("services", "fines", "accidents", "documents", "loans", "trend"):
            self.assertNotIn(key, d)

    def test_other_companies_see_nothing_of_this_fleet(self):
        d = reports.dashboard(self.other_hr, self.today)
        self.assertEqual((d["vehicles_total"], d["services_total"], d["fines_total"], d["leavers"]), (0, 0, 0, []))

    def test_a_vehicle_held_by_someone_who_has_left_is_listed(self):
        assignments.assign_vehicle(self.v, self.ali, self.ago(1), 5000, licence_override=True)
        self.assertEqual(reports.dashboard(self.hr, self.today)["leavers"], [])
        Employee.objects.filter(pk=self.ali.pk).update(status=Employee.Status.SEPARATED)
        leavers = reports.dashboard(self.hr, self.today)["leavers"]
        self.assertEqual([(a.vehicle, a.employee) for a in leavers], [(self.v, self.ali)])
