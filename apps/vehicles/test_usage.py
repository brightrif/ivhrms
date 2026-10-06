from datetime import date, timedelta

from apps.compliance.models import Document, DocumentType
from apps.employees.models import Employee

from . import assignments, odometer, services
from .models import Vehicle
from .tests import VehicleCase
from .usage import OdometerReading


class OdometerTests(VehicleCase):
    def day(self, ago):
        return self.today - timedelta(days=ago)

    def test_it_only_goes_up(self):
        v = self.vehicle()
        odometer.record_odometer(v, 1000, self.day(10))
        with self.assertRaises(services.VehicleError):
            odometer.record_odometer(v, 900, self.today)
        odometer.record_odometer(v, 1000, self.today)                  # the same reading is fine
        v.refresh_from_db()
        self.assertEqual(v.odometer, 1000)

    def test_a_late_entry_must_fit_between_its_neighbours(self):
        v = self.vehicle()
        odometer.record_odometer(v, 1000, self.day(10))
        odometer.record_odometer(v, 2000, self.day(2))
        odometer.record_odometer(v, 1500, self.day(5))                 # an old receipt, in between: fine
        with self.assertRaises(services.VehicleError):
            odometer.record_odometer(v, 900, self.day(5))              # below the earlier reading
        with self.assertRaises(services.VehicleError):
            odometer.record_odometer(v, 2500, self.day(5))             # above the later one
        v.refresh_from_db()
        self.assertEqual(v.odometer, 2000)                             # the vehicle shows the highest

    def test_dates_and_sold_vehicles(self):
        v = self.vehicle()
        with self.assertRaises(services.VehicleError):
            odometer.record_odometer(v, 10, self.today + timedelta(days=1))
        services.mark_sold(v, self.today)
        with self.assertRaises(services.VehicleError):
            odometer.record_odometer(v, 10, self.today)

    def test_a_new_vehicle_with_a_reading_starts_its_history(self):
        v = services.create_vehicle(self.hr, Vehicle(company=self.co, plate_number="9", make="Kia", odometer=5000))
        self.assertEqual(v.odometer_readings.get().source, OdometerReading.Source.INITIAL)
        with self.assertRaises(services.VehicleError):
            odometer.record_odometer(v, 4000, self.today)


class AssignmentTests(VehicleCase):
    def setUp(self):
        super().setUp()
        self.licence_type = DocumentType.objects.get(code="driving-licence")
        self.ali = self.employee("Ali")

    def employee(self, name, company=None, **kw):
        return Employee.objects.create(company=company or self.co, employee_no=f"T-{name}", first_name=name,
                                       joining_date=date(2022, 1, 1), **kw)

    def licence(self, employee, days=300):
        return Document.objects.create(employee=employee, document_type=self.licence_type,
                                       expiry_date=self.today + timedelta(days=days))

    def test_hand_over_and_return(self):
        v = self.vehicle()
        self.licence(self.ali)
        a = assignments.assign_vehicle(v, self.ali, self.today - timedelta(days=5), 1000)
        self.assertEqual(assignments.current_assignment(v), a)
        done = assignments.return_vehicle(a, self.today, 1250)
        self.assertEqual((done.km_driven, assignments.current_assignment(v)), (250, None))
        v.refresh_from_db()
        self.assertEqual(v.odometer, 1250)
        self.assertEqual(v.odometer_readings.count(), 2)

    def test_one_driver_at_a_time_and_a_same_day_hand_over(self):
        v, bob = self.vehicle(), self.employee("Bob")
        self.licence(self.ali)
        self.licence(bob)
        a = assignments.assign_vehicle(v, self.ali, self.today - timedelta(days=3), 100)
        with self.assertRaises(services.VehicleError):
            assignments.assign_vehicle(v, bob, self.today, 100)
        assignments.return_vehicle(a, self.today, 150)
        assignments.assign_vehicle(v, bob, self.today, 150)            # the same day is fine
        self.assertEqual(assignments.current_assignment(v).employee, bob)

    def test_a_hand_over_cannot_start_before_the_previous_return(self):
        v, bob = self.vehicle(), self.employee("Bob")
        self.licence(self.ali)
        self.licence(bob)
        a = assignments.assign_vehicle(v, self.ali, self.today - timedelta(days=10), 100)
        assignments.return_vehicle(a, self.today - timedelta(days=2), 200)
        with self.assertRaises(services.VehicleError):
            assignments.assign_vehicle(v, bob, self.today - timedelta(days=5), 200)

    def test_who_can_be_assigned(self):
        v = self.vehicle()
        self.licence(self.ali)
        gone = self.employee("Gone", status=Employee.Status.SEPARATED)
        elsewhere = self.employee("Zed", company=self.other_co)
        for person in (gone, elsewhere):
            with self.assertRaises(services.VehicleError):
                assignments.assign_vehicle(v, person, self.today, 0, licence_override=True)
        services.mark_sold(v, self.today)
        with self.assertRaises(services.VehicleError):
            assignments.assign_vehicle(v, self.ali, self.today, 0)

    def test_the_driving_licence_is_checked_but_can_be_overridden(self):
        v = self.vehicle()
        with self.assertRaises(assignments.LicenceProblem):            # none on file
            assignments.assign_vehicle(v, self.ali, self.today, 0)
        self.licence(self.ali, days=-1)
        with self.assertRaises(assignments.LicenceProblem):            # expired
            assignments.assign_vehicle(v, self.ali, self.today, 0)
        assignments.assign_vehicle(v, self.ali, self.today, 0, licence_override=True)

    def test_return_rules(self):
        v = self.vehicle()
        self.licence(self.ali)
        a = assignments.assign_vehicle(v, self.ali, self.today - timedelta(days=4), 500)
        with self.assertRaises(services.VehicleError):                 # odometer below the start
            assignments.return_vehicle(a, self.today, 400)
        with self.assertRaises(services.VehicleError):                 # before the hand-over
            assignments.return_vehicle(a, self.today - timedelta(days=9), 600)
        assignments.return_vehicle(a, self.today, 600)
        with self.assertRaises(services.VehicleError):                 # only once
            assignments.return_vehicle(a, self.today, 700)
