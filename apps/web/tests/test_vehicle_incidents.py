from datetime import date, timedelta
from decimal import Decimal

from django.urls import reverse

from apps.compliance.testing import ComplianceCase
from apps.employees.models import Employee
from apps.vehicles import accidents, assignments, fines, maintenance, services
from apps.vehicles.incidents import Accident, Fine
from apps.vehicles.models import Vehicle
from apps.vehicles.upkeep import ServiceRecord


class IncidentPageTests(ComplianceCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.hr)
        self.v = Vehicle.objects.create(company=self.co, plate_number="123456", make="Toyota", model="Hilux",
                                        year=2022)

    def url(self, name, *args):
        return reverse(f"web:{name}", args=args)

    def iso(self, days_ago=0):
        return (self.today - timedelta(days=days_ago)).isoformat()

    def person(self, name="Ali"):
        return Employee.objects.create(company=self.co, employee_no=f"T-{name}", first_name=name,
                                       joining_date=date(2022, 1, 1))

    def fine_data(self, **over):
        data = {"fined_on": self.iso(1), "reference": "T-1", "offence": "Speeding", "location": "Sitra",
                "amount": "20.000", "driver": "", "notes": ""}
        data.update(over)
        return data

    def accident_data(self, **over):
        data = {"occurred_on": self.iso(2), "location": "Isa Town", "description": "Rear-ended at a junction",
                "driver": "", "police_report_no": "PR-7", "fault": "unknown", "third_party_details": "",
                "claim_status": "none", "claim_no": "", "insurance_recovered": "0", "notes": ""}
        data.update(over)
        return data

    def make_fine(self, **over):
        data = dict(vehicle=self.v, fined_on=self.today - timedelta(days=1), offence="Speeding",
                    amount=Decimal("20"), reference="T-9")
        data.update(over)
        return fines.save_fine(Fine(**data))

    def make_accident(self, **over):
        data = dict(vehicle=self.v, occurred_on=self.today - timedelta(days=2), description="Rear-ended")
        data.update(over)
        return accidents.save_accident(Accident(**data))

    # ------------------------------------------------------------ fines

    def test_adding_a_fine(self):
        page = self.url("vehicle_incidents", self.v.pk)
        self.assertEqual(self.client.get(page).status_code, 200)
        add = self.url("vehicle_fine_add", self.v.pk)
        self.assertEqual(self.client.get(add).status_code, 200)
        self.assertRedirects(self.client.post(add, self.fine_data()), page)
        fine = Fine.objects.get()
        self.assertEqual((fine.status, str(fine.amount), fine.company), ("unpaid", "20.000", self.co))
        self.assertContains(self.client.get(page), "Speeding")

    def test_the_fine_is_recorded_against_whoever_had_the_vehicle(self):
        ali = self.person()
        assignments.assign_vehicle(self.v, ali, self.today - timedelta(days=10), 100, licence_override=True)
        self.client.post(self.url("vehicle_fine_add", self.v.pk), self.fine_data(fined_on=self.iso(3)))
        self.assertEqual(Fine.objects.get().driver, ali)
        self.assertContains(self.client.get(self.url("vehicle_incidents", self.v.pk)), "Ali")

    def test_a_bad_fine_is_a_form_error_not_a_crash(self):
        add = self.url("vehicle_fine_add", self.v.pk)
        self.client.post(add, self.fine_data())
        r = self.client.post(add, self.fine_data(reference="t-1"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "already recorded")
        r = self.client.post(add, self.fine_data(reference="T-2", charged_to_employee="on"))
        self.assertContains(r, "Nobody had this vehicle")
        self.assertEqual(self.client.post(add, self.fine_data(reference="T-3", amount="0")).status_code, 200)
        self.assertEqual(Fine.objects.count(), 1)

    def test_paying_a_fine(self):
        fine = self.make_fine()
        pay = self.url("vehicle_fine_pay", fine.pk)
        self.assertEqual(self.client.get(pay).status_code, 200)
        r = self.client.post(pay, {"paid_on": self.iso(0), "amount": "18.000", "reference": "RCPT-5"})
        self.assertRedirects(r, self.url("vehicle_incidents", self.v.pk))
        fine.refresh_from_db()
        self.assertEqual((fine.status, str(fine.paid_amount)), ("paid", "18.000"))
        self.assertRedirects(self.client.get(pay), self.url("vehicle_incidents", self.v.pk))      # only once

    def test_editing_and_cancelling_a_fine(self):
        fine = self.make_fine()
        edit = self.url("vehicle_fine_edit", fine.pk)
        self.assertEqual(self.client.get(edit).status_code, 200)
        self.client.post(edit, self.fine_data(reference="T-9", amount="25.500"))
        fine.refresh_from_db()
        self.assertEqual(str(fine.amount), "25.500")
        void = self.url("vehicle_fine_void", fine.pk)
        self.assertEqual(self.client.post(void, {"reason": ""}).status_code, 200)
        self.assertRedirects(self.client.post(void, {"reason": "dismissed"}), self.url("vehicle_incidents", self.v.pk))
        fine.refresh_from_db()
        self.assertTrue(fine.is_voided)
        self.assertRedirects(self.client.get(edit), self.url("vehicle_incidents", self.v.pk))     # no longer editable

    def test_the_fleet_list_defaults_to_what_is_unpaid(self):
        self.make_fine(reference="T-1", offence="Parking")
        paid = self.make_fine(reference="T-2", offence="Red light")
        fines.pay_fine(paid, self.today, Decimal("20"))
        r = self.client.get(self.url("vehicle_fines"))
        self.assertContains(r, "Parking")
        self.assertNotContains(r, "Red light")
        self.assertContains(self.client.get(self.url("vehicle_fines"), {"status": "paid"}), "Red light")
        self.assertContains(self.client.get(self.url("vehicle_fines"), {"status": "all", "q": "red"}), "Red light")
        self.assertNotContains(self.client.get(self.url("vehicle_fines"), {"status": "all", "q": "red"}), "Parking")

    # ------------------------------------------------------------ accidents

    def test_the_accident_from_report_to_close(self):
        add = self.url("vehicle_accident_add", self.v.pk)
        self.assertEqual(self.client.get(add).status_code, 200)
        r = self.client.post(add, self.accident_data())
        accident = Accident.objects.get()
        page = self.url("vehicle_accident", accident.pk)
        self.assertRedirects(r, page)
        self.assertContains(self.client.get(page), "Rear-ended at a junction")

        edit = self.url("vehicle_accident_edit", accident.pk)
        self.assertEqual(self.client.get(edit).status_code, 200)
        self.client.post(edit, self.accident_data(fault="other", claim_status="paid", claim_no="C-1",
                                                  insurance_recovered="100"))
        accident.refresh_from_db()
        self.assertEqual((accident.fault, str(accident.insurance_recovered)), ("other", "100.000"))

        close = self.url("vehicle_accident_close", accident.pk)
        self.assertEqual(self.client.get(close).status_code, 200)
        self.assertRedirects(self.client.post(close, {"closed_on": self.iso(0)}), page)
        accident.refresh_from_db()
        self.assertEqual(accident.status, "closed")
        self.assertEqual(self.client.get(self.url("vehicle_accident_reopen", accident.pk)).status_code, 405)
        self.assertRedirects(self.client.post(self.url("vehicle_accident_reopen", accident.pk)), page)
        accident.refresh_from_db()
        self.assertEqual((accident.status, accident.closed_on), ("open", None))

    def test_an_accident_with_money_but_no_claim_is_a_form_error(self):
        r = self.client.post(self.url("vehicle_accident_add", self.v.pk), self.accident_data(insurance_recovered="50"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "needs a claim")
        self.assertFalse(Accident.objects.exists())

    def test_linking_a_repair_shows_the_cost(self):
        accident = self.make_accident(claim_status=Accident.Claim.PAID, insurance_recovered=Decimal("100"))
        fix = maintenance.add_service(self.v, serviced_on=self.today, km=0, kind=ServiceRecord.Kind.REPAIR,
                                      parts_cost=Decimal("120"), labour_cost=Decimal("30"), garage="Body shop Isa")
        link = self.url("vehicle_accident_link", accident.pk)
        self.assertContains(self.client.get(link), "Body shop Isa")
        page = self.url("vehicle_accident", accident.pk)
        self.assertRedirects(self.client.post(link, {"repair": fix.pk}), page)
        r = self.client.get(page)
        self.assertContains(r, "Body shop Isa")
        self.assertContains(r, "150.000")                                  # repairs
        self.assertContains(r, "50.000")                                   # net after the 100 recovered
        self.assertRedirects(self.client.post(self.url("vehicle_accident_unlink", accident.pk, fix.pk)), page)
        self.assertEqual(accident.repairs.count(), 0)

    def test_cancelling_an_accident(self):
        accident = self.make_accident()
        void = self.url("vehicle_accident_void", accident.pk)
        self.assertEqual(self.client.post(void, {"reason": ""}).status_code, 200)
        self.assertRedirects(self.client.post(void, {"reason": "wrong vehicle"}),
                             self.url("vehicle_accident", accident.pk))
        accident.refresh_from_db()
        self.assertTrue(accident.is_voided)
        self.assertRedirects(self.client.get(self.url("vehicle_accident_edit", accident.pk)),
                             self.url("vehicle_accident", accident.pk))

    # ------------------------------------------------------------ who can do what

    def test_access_by_role_and_company(self):
        fine, accident = self.make_fine(), self.make_accident()
        incidents = self.url("vehicle_incidents", self.v.pk)
        self.client.logout()
        self.assertEqual(self.client.get(incidents).status_code, 302)                       # sign in first
        self.client.force_login(self.nobody)
        self.assertEqual(self.client.get(incidents).status_code, 403)
        self.client.force_login(self.finance)
        self.assertEqual(self.client.get(incidents).status_code, 200)                       # may look
        self.assertEqual(self.client.get(self.url("vehicle_accident", accident.pk)).status_code, 200)
        self.assertEqual(self.client.get(self.url("vehicle_fine_add", self.v.pk)).status_code, 403)
        self.assertEqual(self.client.get(self.url("vehicle_fine_edit", fine.pk)).status_code, 403)
        self.assertEqual(self.client.get(self.url("vehicle_accident_add", self.v.pk)).status_code, 403)
        self.assertEqual(self.client.get(self.url("vehicle_fine_pay", fine.pk)).status_code, 200)     # Finance pays
        self.client.force_login(self.boss)
        self.assertEqual(self.client.get(self.url("vehicle_fine_pay", fine.pk)).status_code, 403)     # Management only looks
        self.client.force_login(self.other_hr)
        for url in (incidents, self.url("vehicle_fine_pay", fine.pk), self.url("vehicle_accident", accident.pk)):
            self.assertEqual(self.client.get(url).status_code, 404, url)                   # someone else's vehicle

    def test_a_sold_vehicle_still_takes_fines_and_accidents(self):
        services.mark_sold(self.v, self.today)
        self.assertEqual(self.client.get(self.url("vehicle_fine_add", self.v.pk)).status_code, 200)
        self.assertEqual(self.client.get(self.url("vehicle_accident_add", self.v.pk)).status_code, 200)

    def test_every_form_places_every_field_it_has(self):
        fine, accident = self.make_fine(), self.make_accident()
        pages = [self.url("vehicle_fine_add", self.v.pk), self.url("vehicle_fine_edit", fine.pk),
                 self.url("vehicle_fine_pay", fine.pk), self.url("vehicle_fine_void", fine.pk),
                 self.url("vehicle_accident_add", self.v.pk), self.url("vehicle_accident_edit", accident.pk),
                 self.url("vehicle_accident_close", accident.pk), self.url("vehicle_accident_link", accident.pk),
                 self.url("vehicle_accident_void", accident.pk)]
        for url in pages:
            r = self.client.get(url)
            placed = {n for section in r.context["layout"] for row in section["rows"] for n in row}
            self.assertEqual(placed, set(r.context["form"].fields), url)
