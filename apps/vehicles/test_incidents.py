from decimal import Decimal

from apps.employees.models import Employee

from . import accidents, assignments, fines, maintenance, services
from .incidents import Accident, Fine
from .test_upkeep import UpkeepCase
from .upkeep import ServiceRecord


class FineTests(UpkeepCase):
    def fine(self, v, ago=1, **kw):
        data = dict(vehicle=v, fined_on=self.ago(ago), offence="Speeding", amount=Decimal("20.000"))
        data.update(kw)
        return Fine(**data)

    def held_ten_to_five_days_ago(self, v, who):
        a = assignments.assign_vehicle(v, who, self.ago(10), 100, licence_override=True)
        assignments.return_vehicle(a, self.ago(5), 200)

    def test_the_fine_finds_whoever_had_the_vehicle_that_day(self):
        v, ali = self.vehicle(), self.person()
        self.held_ten_to_five_days_ago(v, ali)
        self.assertEqual(fines.save_fine(self.fine(v, ago=7)).driver, ali)
        self.assertIsNone(fines.save_fine(self.fine(v, ago=2)).driver)             # nobody had it that day

    def test_a_driver_who_has_since_left_still_gets_the_fine(self):
        v, ali = self.vehicle(), self.person()
        self.held_ten_to_five_days_ago(v, ali)
        Employee.objects.filter(pk=ali.pk).update(status=Employee.Status.SEPARATED)
        f = fines.save_fine(self.fine(v, ago=7, charged_to_employee=True))
        self.assertEqual((f.driver, f.charged_to_employee), (ali, True))

    def test_charging_a_driver_needs_one(self):
        v, ali = self.vehicle(), self.person()
        with self.assertRaises(services.VehicleError):
            fines.save_fine(self.fine(v, charged_to_employee=True))
        self.assertTrue(fines.save_fine(self.fine(v, driver=ali, charged_to_employee=True)).charged_to_employee)

    def test_the_driver_must_work_for_the_same_company(self):
        v = self.vehicle()
        stranger = self.person("Zed")
        Employee.objects.filter(pk=stranger.pk).update(company=self.other_co)
        stranger.refresh_from_db()
        with self.assertRaises(services.VehicleError):
            fines.save_fine(self.fine(v, driver=stranger))

    def test_the_same_ticket_cannot_be_entered_twice(self):
        a, b = self.vehicle("1"), self.vehicle("2")
        first = fines.save_fine(self.fine(a, reference="T-100"))
        with self.assertRaises(services.VehicleError):
            fines.save_fine(self.fine(a, reference="t-100"))
        fines.save_fine(self.fine(b, reference="T-100"))                           # another vehicle: fine
        fines.void_fine(first, "entered by mistake")
        fines.save_fine(self.fine(a, reference="T-100"))                           # a cancelled one frees the number

    def test_amount_and_date_rules(self):
        v = self.vehicle()
        for bad in (self.fine(v, amount=Decimal("0")), self.fine(v, ago=-1)):
            with self.assertRaises(services.VehicleError):
                fines.save_fine(bad)

    def test_paying_a_fine(self):
        v = self.vehicle()
        f = fines.save_fine(self.fine(v, ago=5))
        with self.assertRaises(services.VehicleError):
            fines.pay_fine(f, self.ago(9), Decimal("20"))                          # before the offence
        with self.assertRaises(services.VehicleError):
            fines.pay_fine(f, self.ago(-1), Decimal("20"))                         # tomorrow
        paid = fines.pay_fine(f, self.today, Decimal("18.000"), " REF-9 ")
        self.assertEqual((paid.status, paid.display_amount, paid.payment_reference),
                         (Fine.Status.PAID, Decimal("18.000"), "REF-9"))
        with self.assertRaises(services.VehicleError):
            fines.pay_fine(f, self.today, Decimal("18"))                           # only once

    def test_only_an_unpaid_fine_can_be_edited_and_a_paid_one_can_still_be_cancelled(self):
        v = self.vehicle()
        f = fines.save_fine(self.fine(v, reference="T-1"))
        f.amount = Decimal("25.000")
        self.assertEqual(fines.save_fine(f).amount, Decimal("25.000"))             # its own number is not a duplicate
        fines.pay_fine(f, self.today, Decimal("25"))
        f.offence = "Changed"
        with self.assertRaises(services.VehicleError):
            fines.save_fine(f)
        fines.void_fine(f, "paid twice by mistake")
        with self.assertRaises(services.VehicleError):
            fines.void_fine(f, "again")
        with self.assertRaises(services.VehicleError):
            fines.pay_fine(f, self.today, Decimal("25"))

    def test_a_reason_is_needed_to_cancel(self):
        f = fines.save_fine(self.fine(self.vehicle()))
        with self.assertRaises(services.VehicleError):
            fines.void_fine(f, "  ")

    def test_the_unpaid_summary(self):
        v = self.vehicle()
        a = fines.save_fine(self.fine(v, amount=Decimal("20.500")))
        b = fines.save_fine(self.fine(v, amount=Decimal("10.250")))
        c = fines.save_fine(self.fine(v, amount=Decimal("99")))
        fines.pay_fine(a, self.today, Decimal("20.5"))
        fines.void_fine(c, "dismissed")
        self.assertEqual(fines.unpaid_summary(v), {"count": 1, "total": b.amount})

    def test_a_sold_vehicle_still_takes_fines_because_they_arrive_late(self):
        v = self.vehicle()
        services.mark_sold(v, self.today)
        self.assertTrue(fines.save_fine(self.fine(v, ago=30)).pk)


class AccidentTests(UpkeepCase):
    def accident(self, v, ago=3, **kw):
        data = dict(vehicle=v, occurred_on=self.ago(ago), description="Rear-ended at a junction")
        data.update(kw)
        return Accident(**data)

    def repair(self, v, parts="120", labour="30", kind=ServiceRecord.Kind.REPAIR):
        return maintenance.add_service(v, serviced_on=self.today, km=0, kind=kind, parts_cost=Decimal(parts),
                                       labour_cost=Decimal(labour), garage="Body shop")

    def test_the_driver_is_found_from_the_assignment_history(self):
        v, ali = self.vehicle(), self.person()
        assignments.assign_vehicle(v, ali, self.ago(10), 100, licence_override=True)
        self.assertEqual(accidents.save_accident(self.accident(v)).driver, ali)

    def test_claim_rules(self):
        v = self.vehicle()
        with self.assertRaises(services.VehicleError):                             # money, but no claim
            accidents.save_accident(self.accident(v, insurance_recovered=Decimal("50")))
        ok = accidents.save_accident(self.accident(v, claim_status=Accident.Claim.PAID,
                                                   insurance_recovered=Decimal("50")))
        self.assertEqual(ok.insurance_recovered, Decimal("50"))
        with self.assertRaises(services.VehicleError):
            accidents.save_accident(self.accident(v, ago=-1))                      # tomorrow

    def test_closing_and_reopening(self):
        a = accidents.save_accident(self.accident(self.vehicle(), ago=5))
        with self.assertRaises(services.VehicleError):
            accidents.close_accident(a, self.ago(9))                               # before it happened
        with self.assertRaises(services.VehicleError):
            accidents.reopen_accident(a)                                           # not closed yet
        closed = accidents.close_accident(a, self.today)
        self.assertEqual((closed.status, closed.closed_on), (Accident.Status.CLOSED, self.today))
        with self.assertRaises(services.VehicleError):
            accidents.close_accident(a, self.today)
        reopened = accidents.reopen_accident(a)
        self.assertEqual((reopened.status, reopened.closed_on), (Accident.Status.OPEN, None))

    def test_cancelling(self):
        a = accidents.save_accident(self.accident(self.vehicle()))
        with self.assertRaises(services.VehicleError):
            accidents.void_accident(a, "")
        accidents.void_accident(a, "reported on the wrong vehicle")
        a.refresh_from_db()
        for action in (lambda: accidents.save_accident(a), lambda: accidents.close_accident(a, self.today),
                       lambda: accidents.void_accident(a, "again")):
            with self.assertRaises(services.VehicleError):
                action()

    def test_repairs_show_what_the_accident_cost(self):
        v = self.vehicle()
        a = accidents.save_accident(self.accident(v, claim_status=Accident.Claim.PAID,
                                                  insurance_recovered=Decimal("100")))
        fix = self.repair(v)
        accidents.link_repair(a, fix)
        a = Accident.objects.get(pk=a.pk)
        self.assertEqual((a.repair_cost, a.net_cost), (Decimal("150"), Decimal("50")))

        maintenance.void_service(fix, "wrong vehicle")                             # a cancelled repair stops counting
        self.assertEqual(Accident.objects.get(pk=a.pk).repair_cost, Decimal("0"))

    def test_which_repairs_can_be_linked(self):
        a_vehicle, b_vehicle = self.vehicle("1"), self.vehicle("2")
        first = accidents.save_accident(self.accident(a_vehicle))
        second = accidents.save_accident(self.accident(a_vehicle, ago=2))
        fix = self.repair(a_vehicle)
        accidents.link_repair(first, fix)
        with self.assertRaises(services.VehicleError):                             # already linked elsewhere
            accidents.link_repair(second, fix)
        with self.assertRaises(services.VehicleError):                             # another vehicle's repair
            accidents.link_repair(first, self.repair(b_vehicle))
        with self.assertRaises(services.VehicleError):                             # a routine service is not a repair
            accidents.link_repair(first, self.repair(a_vehicle, kind=ServiceRecord.Kind.SCHEDULED))
        accidents.unlink_repair(first, fix)
        accidents.link_repair(second, fix)                                         # free again

    def test_a_sold_vehicle_still_takes_accidents(self):
        v = self.vehicle()
        services.mark_sold(v, self.today)
        self.assertTrue(accidents.save_accident(self.accident(v, ago=30)).pk)
