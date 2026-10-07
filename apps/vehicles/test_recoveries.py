from datetime import timedelta
from decimal import Decimal as D

from . import assignments, fines, recoveries, reports, services
from .incidents import Fine
from .test_upkeep import UpkeepCase


class RecoveryTests(UpkeepCase):
    def setUp(self):
        super().setUp()
        self.v, self.ali, self.bob = self.vehicle(), self.person("Ali"), self.person("Bob")
        a = assignments.assign_vehicle(self.v, self.ali, self.ago(30), 100, licence_override=True)
        assignments.return_vehicle(a, self.ago(10), 200)
        assignments.assign_vehicle(self.v, self.bob, self.ago(10), 200, licence_override=True)

    def charged(self, ago, amount="20", ref="", **kw):
        return fines.save_fine(Fine(vehicle=self.v, fined_on=self.ago(ago), offence="Speeding", amount=D(amount),
                                    reference=ref, charged_to_employee=True, **kw))

    def test_the_list_groups_open_fines_by_person_largest_first(self):
        a1, a2, b1 = self.charged(20, "20", "A1"), self.charged(15, "15.500", "A2"), self.charged(2, "40", "B1")
        self.assertEqual([a1.driver, a2.driver, b1.driver], [self.ali, self.ali, self.bob])
        people = recoveries.to_recover(self.finance)
        self.assertEqual([(p.employee, p.total, len(p.fines)) for p in people],
                         [(self.bob, D("40"), 1), (self.ali, D("35.500"), 2)])
        self.assertEqual(recoveries.to_recover(self.other_hr), [])                      # only your own companies
        fines.save_fine(Fine(vehicle=self.v, fined_on=self.ago(1), offence="Parking", amount=D("5"), reference="N1"))
        self.assertEqual(len(recoveries.to_recover(self.finance)), 2)                   # a fine nobody pays back is not listed

    def test_marking_a_fine_recovered_and_undoing_it(self):
        fine = self.charged(20, ref="A1")
        with self.assertRaises(services.VehicleError):
            recoveries.mark_recovered(fine, self.ago(30))                               # before the offence
        with self.assertRaises(services.VehicleError):
            recoveries.mark_recovered(fine, self.today + timedelta(days=1))             # in the future
        done = recoveries.mark_recovered(fine, self.today, " from March salary ")
        self.assertEqual((done.recovered_on, done.recovered_note), (self.today, "from March salary"))
        self.assertEqual(recoveries.to_recover(self.finance), [])
        self.assertEqual([f.pk for f in recoveries.recovered(self.finance)], [fine.pk])
        with self.assertRaises(services.VehicleError):
            recoveries.mark_recovered(fine, self.today)                                 # only once
        with self.assertRaises(services.VehicleError):
            recoveries.undo_recovery(fine, " ")
        undone = recoveries.undo_recovery(fine, "paid the wrong fine")
        self.assertIsNone(undone.recovered_on)
        self.assertEqual(len(recoveries.to_recover(self.finance)), 1)

    def test_only_fines_charged_to_a_driver_can_be_recovered(self):
        plain = fines.save_fine(Fine(vehicle=self.v, fined_on=self.ago(1), offence="Parking", amount=D("5"),
                                     reference="N1"))
        with self.assertRaises(services.VehicleError):
            recoveries.mark_recovered(plain, self.today)
        cancelled = self.charged(3, ref="C1")
        fines.void_fine(cancelled, "dismissed")
        with self.assertRaises(services.VehicleError):
            recoveries.mark_recovered(cancelled, self.today)

    def test_a_recovered_fine_is_locked_until_the_recovery_is_undone(self):
        fine = self.charged(20, ref="A1")
        recoveries.mark_recovered(fine, self.today)
        fine.offence = "Changed"
        with self.assertRaises(services.VehicleError):
            fines.save_fine(fine)
        recoveries.undo_recovery(fine, "mistake")
        fine.refresh_from_db()
        self.assertTrue(fines.save_fine(fine).pk)

    def test_the_cost_report_stops_counting_a_recovered_fine_as_to_recover(self):
        fine = self.charged(2, "20", "A1")
        year = self.today.year
        before = reports.cost_report(self.finance, year)["rows"][0]
        self.assertEqual((before.fines, before.recoverable), (D("20"), D("20")))
        recoveries.mark_recovered(fine, self.today)
        after = reports.cost_report(self.finance, year)["rows"][0]
        self.assertEqual((after.fines, after.recoverable), (D("20"), D("0")))           # still a cost, no longer to recover
