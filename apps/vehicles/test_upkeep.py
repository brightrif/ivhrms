from datetime import date, timedelta
from decimal import Decimal

from django.core import mail

from apps.employees.models import Employee

from . import assignments, fuel, maintenance, odometer, services
from .tests import VehicleCase
from .upkeep import FuelFill, PlanAlertLog, ServicePlan
from .usage import OdometerReading


class UpkeepCase(VehicleCase):
    def ago(self, days):
        return self.today - timedelta(days=days)

    def person(self, name="Ali"):
        return Employee.objects.create(company=self.co, employee_no=f"T-{name}", first_name=name,
                                       joining_date=date(2022, 1, 1))


class VoidingTests(UpkeepCase):
    def test_cancelling_a_wrong_reading_lowers_the_odometer_and_frees_the_right_number(self):
        v = self.vehicle()
        odometer.record_odometer(v, 1000, self.ago(5))
        typo = odometer.record_odometer(v, 150000, self.ago(1))
        v.refresh_from_db()
        self.assertEqual(v.odometer, 150000)
        odometer.void_reading(typo, "typed an extra zero")
        v.refresh_from_db()
        self.assertEqual(v.odometer, 1000)
        odometer.record_odometer(v, 15000, self.ago(1))                 # the correct figure is accepted now
        v.refresh_from_db()
        self.assertEqual(v.odometer, 15000)


class FuelTests(UpkeepCase):
    def fill(self, v, ago, km, litres, cost, full=True):
        return fuel.add_fill(v, filled_on=self.ago(ago), litres=Decimal(litres), cost=Decimal(cost), km=km,
                             full_tank=full)

    def test_a_fill_moves_the_odometer_and_remembers_who_had_the_vehicle(self):
        v, ali = self.vehicle(), self.person()
        assignments.assign_vehicle(v, ali, self.ago(10), 1000, licence_override=True)
        f = self.fill(v, 3, 1200, "40", "12")
        v.refresh_from_db()
        self.assertEqual((v.odometer, f.driver, f.reading.source), (1200, ali, OdometerReading.Source.FUEL))
        before_handover = self.fill(v, 12, 900, "30", "9")              # an old receipt, before Ali had it
        self.assertIsNone(before_handover.driver)

    def test_consumption_runs_from_full_tank_to_full_tank(self):
        v = self.vehicle()
        self.fill(v, 20, 1000, "40", "12.000")
        self.fill(v, 10, 1400, "30", "9.000")                           # 400 km on 30 L
        self.fill(v, 5, 1500, "10", "3.000", full=False)                # a part fill counts towards the next full one
        self.fill(v, 1, 1700, "25", "7.500")                            # 300 km on 10 + 25 L
        stats = fuel.fuel_stats(v)
        newest, partial, middle, first = stats["rows"]
        self.assertAlmostEqual(float(newest.km_per_litre), 300 / 35, places=3)
        self.assertAlmostEqual(float(middle.km_per_litre), 400 / 30, places=3)
        self.assertIsNone(partial.km_per_litre)
        self.assertIsNone(first.km_per_litre)
        self.assertAlmostEqual(float(stats["avg_kmpl"]), 700 / 65, places=3)
        self.assertAlmostEqual(float(stats["cost_per_km"]), 19.5 / 700, places=4)
        self.assertEqual(stats["litres_12m"], Decimal("105"))

    def test_a_cancelled_entry_is_kept_but_not_counted(self):
        v = self.vehicle()
        self.fill(v, 20, 1000, "40", "12")
        wrong = self.fill(v, 10, 1400, "30", "9")
        self.fill(v, 1, 1700, "25", "7.5")
        fuel.void_fill(wrong, "entered twice")
        v.refresh_from_db()
        stats = fuel.fuel_stats(v)
        self.assertEqual(len(stats["rows"]), 3)
        self.assertTrue(next(r for r in stats["rows"] if r.pk == wrong.pk).is_voided)
        self.assertAlmostEqual(float(stats["avg_kmpl"]), 700 / 25, places=3)
        with self.assertRaises(services.VehicleError):
            fuel.void_fill(wrong, "again")
        with self.assertRaises(services.VehicleError):
            fuel.void_fill(FuelFill.objects.exclude(pk=wrong.pk).first(), "  ")      # a reason is required

    def test_entry_rules(self):
        v = self.vehicle()
        with self.assertRaises(services.VehicleError):
            self.fill(v, 1, 100, "0", "5")                              # no litres
        with self.assertRaises(services.VehicleError):
            self.fill(v, -1, 100, "20", "5")                            # tomorrow
        self.fill(v, 5, 500, "20", "6")
        with self.assertRaises(services.VehicleError):
            self.fill(v, 1, 400, "20", "6")                             # the odometer went down
        services.mark_sold(v, self.today)
        with self.assertRaises(services.VehicleError):
            self.fill(v, 0, 600, "20", "6")


class PlanStatusTests(UpkeepCase):
    def plan(self, v, **kw):
        data = dict(vehicle=v, name="Oil change", every_km=5000, every_months=None, baseline_on=self.ago(10),
                    baseline_km=0)
        data.update(kw)
        return ServicePlan.objects.create(**data)

    def state(self, v):
        v.refresh_from_db()
        return maintenance.vehicle_plans(v, self.today)[0].status

    def test_distance_decides_when_there_is_no_time_limit(self):
        v = self.vehicle()
        self.plan(v)
        self.assertEqual(self.state(v).state, maintenance.OK)
        odometer.record_odometer(v, 4600, self.today)
        s = self.state(v)
        self.assertEqual((s.state, s.km_left, s.due_km), (maintenance.SOON, 400, 5000))
        odometer.record_odometer(v, 5100, self.today)
        self.assertEqual((self.state(v).state, self.state(v).km_left), (maintenance.OVERDUE, -100))

    def test_time_decides_when_there_is_no_distance_limit(self):
        v = self.vehicle()
        p = self.plan(v, every_km=None, every_months=6, baseline_on=self.ago(120))
        self.assertEqual(self.state(v).state, maintenance.OK)
        p.baseline_on = self.ago(170)
        p.save()
        self.assertEqual(self.state(v).state, maintenance.SOON)
        p.baseline_on = self.ago(220)
        p.save()
        self.assertEqual(self.state(v).state, maintenance.OVERDUE)

    def test_whichever_comes_first_wins(self):
        v = self.vehicle()
        self.plan(v, every_months=6, baseline_on=self.ago(220))      # late by time, fine by distance
        self.assertEqual(self.state(v).state, maintenance.OVERDUE)

    def test_a_service_resets_the_counters_and_cancelling_it_puts_them_back(self):
        v = self.vehicle()
        p = self.plan(v)
        odometer.record_odometer(v, 5100, self.today)
        rec = maintenance.add_service(v, serviced_on=self.today, km=5200, plans=[p], garage="Main St Garage",
                                      parts_cost=Decimal("18.500"), labour_cost=Decimal("6"))
        s = self.state(v)
        self.assertEqual((s.state, s.due_km, s.last_km), (maintenance.OK, 10200, 5200))
        self.assertEqual(rec.total_cost, Decimal("24.500"))
        maintenance.void_service(rec, "wrong vehicle")
        s = self.state(v)
        self.assertEqual((s.state, v.odometer), (maintenance.OVERDUE, 5100))

    def test_service_entry_rules(self):
        a, b = self.vehicle("1"), self.vehicle("2")
        mine, theirs = self.plan(a), self.plan(b)
        with self.assertRaises(services.VehicleError):
            maintenance.add_service(a, serviced_on=self.today, km=10, plans=[theirs])      # someone else's plan
        mine.is_active = False
        mine.save()
        with self.assertRaises(services.VehicleError):
            maintenance.add_service(a, serviced_on=self.today, km=10, plans=[mine])        # switched off
        with self.assertRaises(services.VehicleError):
            maintenance.add_service(a, serviced_on=self.ago(-1), km=10)                    # tomorrow
        with self.assertRaises(services.VehicleError):
            maintenance.void_service(maintenance.add_service(a, serviced_on=self.today, km=10), "")

    def test_the_due_list_shows_only_what_needs_attention_and_only_to_the_right_people(self):
        v, other = self.vehicle("1"), self.vehicle("2")
        self.plan(v)
        self.plan(other, name="Tyres")
        odometer.record_odometer(v, 4800, self.today)
        self.assertEqual([p.vehicle for p in maintenance.due_plans(self.hr, self.today)], [v])
        self.assertEqual(maintenance.due_plans(self.other_hr, self.today), [])
        services.mark_sold(v, self.today)
        self.assertEqual(maintenance.due_plans(self.hr, self.today), [])


class ServiceAlertTests(UpkeepCase):
    def setUp(self):
        super().setUp()
        self.v = self.vehicle()
        self.plan = ServicePlan.objects.create(vehicle=self.v, name="Oil change", every_km=5000,
                                               baseline_on=self.ago(10), baseline_km=0)

    def to(self):
        return sorted(m.to[0] for m in mail.outbox)

    def test_one_email_when_due_soon_and_one_more_when_overdue_and_never_a_repeat(self):
        odometer.record_odometer(self.v, 4600, self.today)
        stats = maintenance.run_service_scan(self.today)
        self.assertEqual((stats["alerts"], stats["emails"]), (1, 1))
        self.assertEqual(self.to(), ["hr@example.com"])
        self.assertIn("due soon", mail.outbox[0].subject)
        self.assertEqual(maintenance.run_service_scan(self.today)["alerts"], 0)             # same day again: nothing

        odometer.record_odometer(self.v, 5100, self.today)
        stats = maintenance.run_service_scan(self.today)
        self.assertEqual(stats["alerts"], 1)
        self.assertIn("OVERDUE", mail.outbox[-1].subject)
        self.assertIn("boss@example.com", self.to())                                        # overdue goes to Management too
        self.assertEqual(maintenance.run_service_scan(self.today)["alerts"], 0)

    def test_the_current_driver_is_told_too(self):
        ali = self.person()
        ali.email = "ali@example.com"
        ali.save()
        assignments.assign_vehicle(self.v, ali, self.ago(5), 100, licence_override=True)
        odometer.record_odometer(self.v, 4700, self.today)
        maintenance.run_service_scan(self.today)
        self.assertEqual(self.to(), ["ali@example.com", "hr@example.com"])

    def test_a_new_service_starts_a_new_cycle(self):
        odometer.record_odometer(self.v, 4600, self.today)
        maintenance.run_service_scan(self.today)
        maintenance.add_service(self.v, serviced_on=self.today, km=4650, plans=[self.plan])
        self.assertEqual(maintenance.run_service_scan(self.today)["alerts"], 0)             # fine for another 5,000 km
        odometer.record_odometer(self.v, 9400, self.today)
        self.assertEqual(maintenance.run_service_scan(self.today)["alerts"], 1)             # due again: a fresh alert
        self.assertEqual(PlanAlertLog.objects.filter(plan=self.plan).count(), 2)

    def test_sold_vehicles_and_switched_off_plans_are_ignored(self):
        odometer.record_odometer(self.v, 4900, self.today)
        self.plan.is_active = False
        self.plan.save()
        self.assertEqual(maintenance.run_service_scan(self.today)["checked"], 0)
        self.plan.is_active = True
        self.plan.save()
        services.mark_sold(self.v, self.today)
        self.assertEqual(maintenance.run_service_scan(self.today)["checked"], 0)
        self.assertEqual(mail.outbox, [])
