from datetime import timedelta

from django.urls import reverse

from apps.compliance.testing import ComplianceCase
from apps.vehicles import odometer, services
from apps.vehicles.models import Vehicle
from apps.vehicles.upkeep import FuelFill, ServicePlan, ServiceRecord


class UpkeepPageTests(ComplianceCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.hr)
        self.v = Vehicle.objects.create(company=self.co, plate_number="123456", make="Toyota", model="Hilux",
                                        year=2022)

    def url(self, name, *args):
        return reverse(f"web:{name}", args=args)

    def iso(self, days_ago=0):
        return (self.today - timedelta(days=days_ago)).isoformat()

    def fuel_data(self, **over):
        data = {"filled_on": self.iso(1), "odometer": "1200", "litres": "40.5", "cost": "12.150",
                "full_tank": "on", "station": "Bapco", "notes": ""}
        data.update(over)
        return data

    def plan_data(self, **over):
        data = {"name": "Oil change", "every_km": "5000", "every_months": "6", "warn_km": "500", "warn_days": "30",
                "baseline_on": self.iso(10), "baseline_km": "0", "is_active": "on"}
        data.update(over)
        return data

    def make_plan(self, **over):
        data = dict(vehicle=self.v, name="Oil change", every_km=5000, baseline_on=self.today - timedelta(days=10),
                    baseline_km=0)
        data.update(over)
        return ServicePlan.objects.create(**data)

    # ------------------------------------------------------------ fuel

    def test_adding_a_fill_updates_the_odometer(self):
        self.assertEqual(self.client.get(self.url("vehicle_fuel", self.v.pk)).status_code, 200)
        add = self.url("vehicle_fuel_add", self.v.pk)
        self.assertEqual(self.client.get(add).status_code, 200)
        self.assertRedirects(self.client.post(add, self.fuel_data()), self.url("vehicle_fuel", self.v.pk))
        fill = FuelFill.objects.get()
        self.v.refresh_from_db()
        self.assertEqual((self.v.odometer, fill.full_tank, str(fill.litres)), (1200, True, "40.50"))
        self.assertContains(self.client.get(self.url("vehicle_fuel", self.v.pk)), "Bapco")

    def test_a_part_fill_is_a_checkbox_left_empty(self):
        data = self.fuel_data()
        del data["full_tank"]
        self.client.post(self.url("vehicle_fuel_add", self.v.pk), data)
        self.assertFalse(FuelFill.objects.get().full_tank)

    def test_a_bad_fill_is_a_form_error_not_a_crash(self):
        add = self.url("vehicle_fuel_add", self.v.pk)
        self.client.post(add, self.fuel_data())
        r = self.client.post(add, self.fuel_data(odometer="900", filled_on=self.iso(0)))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "cannot go down")
        self.assertEqual(FuelFill.objects.count(), 1)
        self.assertEqual(self.client.post(add, self.fuel_data(litres="0")).status_code, 200)

    def test_cancelling_a_fill_needs_a_reason_and_puts_the_odometer_back(self):
        self.client.post(self.url("vehicle_fuel_add", self.v.pk), self.fuel_data())
        fill = FuelFill.objects.get()
        void = self.url("vehicle_fuel_void", fill.pk)
        self.assertEqual(self.client.get(void).status_code, 200)
        self.assertEqual(self.client.post(void, {"reason": ""}).status_code, 200)
        self.assertRedirects(self.client.post(void, {"reason": "entered twice"}), self.url("vehicle_fuel", self.v.pk))
        fill.refresh_from_db()
        self.v.refresh_from_db()
        self.assertEqual((fill.is_voided, self.v.odometer), (True, 0))
        self.assertContains(self.client.get(self.url("vehicle_fuel", self.v.pk)), "entered twice")
        self.assertRedirects(self.client.get(void), self.url("vehicle_fuel", self.v.pk))      # only once

    # ------------------------------------------------------------ maintenance

    def test_plan_then_service_then_cancel(self):
        page = self.url("vehicle_service", self.v.pk)
        self.assertEqual(self.client.get(page).status_code, 200)
        self.assertRedirects(self.client.post(self.url("vehicle_plan_add", self.v.pk), self.plan_data()), page)
        plan = ServicePlan.objects.get()
        self.assertContains(self.client.get(page), "Oil change")

        add = self.url("vehicle_service_add", self.v.pk)
        self.assertEqual(self.client.get(add).status_code, 200)
        data = {"serviced_on": self.iso(0), "odometer": "1200", "kind": "scheduled", "garage": "Main St Garage",
                "description": "Oil and filter", "parts_cost": "18.5", "labour_cost": "6", "invoice_no": "A-1",
                "plans": [plan.pk]}
        self.assertRedirects(self.client.post(add, data), page)
        record = ServiceRecord.objects.get()
        self.assertEqual((list(record.plans.all()), str(record.total_cost)), ([plan], "24.500"))
        self.assertContains(self.client.get(page), "Main St Garage")

        void = self.url("vehicle_service_void", record.pk)
        self.assertEqual(self.client.get(void).status_code, 200)
        self.assertRedirects(self.client.post(void, {"reason": "wrong vehicle"}), page)
        record.refresh_from_db()
        self.v.refresh_from_db()
        self.assertEqual((record.is_voided, self.v.odometer), (True, 0))

    def test_a_plan_needs_an_interval_and_a_new_name(self):
        add = self.url("vehicle_plan_add", self.v.pk)
        r = self.client.post(add, self.plan_data(every_km="", every_months=""))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Set a distance")
        self.assertFalse(ServicePlan.objects.exists())
        self.client.post(add, self.plan_data())
        r = self.client.post(add, self.plan_data(name="oil change"))
        self.assertContains(r, "already has an active plan")
        self.assertEqual(ServicePlan.objects.count(), 1)

    def test_editing_a_plan_and_switching_it_off(self):
        plan = self.make_plan()
        edit = self.url("vehicle_plan_edit", plan.pk)
        self.assertEqual(self.client.get(edit).status_code, 200)
        data = self.plan_data(every_km="8000")
        del data["is_active"]
        self.assertRedirects(self.client.post(edit, data), self.url("vehicle_service", self.v.pk))
        plan.refresh_from_db()
        self.assertEqual((plan.every_km, plan.is_active), (8000, False))

    def test_the_due_list_shows_what_needs_attention(self):
        self.make_plan()
        due = self.url("vehicle_service_due")
        self.assertContains(self.client.get(due), "Nothing is due")
        odometer.record_odometer(self.v, 5100, self.today)
        r = self.client.get(due)
        self.assertContains(r, "123456")
        self.assertContains(r, "Overdue")

    # ------------------------------------------------------------ who can do what

    def test_access_by_role_and_company(self):
        fuel, add = self.url("vehicle_fuel", self.v.pk), self.url("vehicle_fuel_add", self.v.pk)
        self.client.logout()
        self.assertEqual(self.client.get(fuel).status_code, 302)                   # sign in first
        self.client.force_login(self.nobody)
        self.assertEqual(self.client.get(fuel).status_code, 403)
        self.client.force_login(self.finance)
        self.assertEqual(self.client.get(fuel).status_code, 200)                   # may look
        self.assertEqual(self.client.get(add).status_code, 403)                    # may not add
        self.assertEqual(self.client.post(add, self.fuel_data()).status_code, 403)
        self.client.force_login(self.other_hr)
        self.assertEqual(self.client.get(fuel).status_code, 404)                   # someone else's vehicle
        self.assertEqual(self.client.get(self.url("vehicle_service", self.v.pk)).status_code, 404)

    def test_a_sold_vehicle_takes_no_new_entries(self):
        services.mark_sold(self.v, self.today)
        for name in ("vehicle_fuel_add", "vehicle_service_add", "vehicle_plan_add"):
            r = self.client.get(self.url(name, self.v.pk))
            self.assertEqual(r.status_code, 302, name)

    def test_every_form_places_every_field_it_has(self):
        plan = self.make_plan()
        pages = [self.url("vehicle_fuel_add", self.v.pk), self.url("vehicle_plan_add", self.v.pk),
                 self.url("vehicle_plan_edit", plan.pk), self.url("vehicle_service_add", self.v.pk)]
        self.client.post(self.url("vehicle_fuel_add", self.v.pk), self.fuel_data())
        pages.append(self.url("vehicle_fuel_void", FuelFill.objects.get().pk))
        for url in pages:
            r = self.client.get(url)
            placed = {n for section in r.context["layout"] for row in section["rows"] for n in row}
            self.assertEqual(placed, set(r.context["form"].fields), url)


class VehiclePagesStillOpenTests(ComplianceCase):
    """The list and detail pages now carry the maintenance and fuel cards; a smoke test for both."""

    def setUp(self):
        super().setUp()
        self.client.force_login(self.hr)
        self.v = Vehicle.objects.create(company=self.co, plate_number="123456", make="Toyota", model="Hilux",
                                        year=2022)

    def test_list_and_detail_render(self):
        r = self.client.get(reverse("web:vehicle_list"))
        self.assertContains(r, "123456")
        self.assertContains(r, "Maintenance due")
        r = self.client.get(reverse("web:vehicle_detail", args=[self.v.pk]))
        self.assertEqual(r.status_code, 200)
        for text in ("Maintenance", "Fuel", "Odometer", "Driver history"):
            self.assertContains(r, text)
