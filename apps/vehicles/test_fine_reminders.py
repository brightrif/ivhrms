from datetime import timedelta
from decimal import Decimal as D

from django.core import mail

from . import fines, finereminders, services
from .incidents import Fine, FineReminderLog
from .models import Vehicle
from .test_upkeep import UpkeepCase


class FineReminderTests(UpkeepCase):
    def setUp(self):
        super().setUp()
        self.v = self.vehicle()
        self.n = 0

    def fine(self, days_old, v=None, **kw):
        self.n += 1
        data = dict(vehicle=v or self.v, fined_on=self.ago(days_old), offence=f"Offence {self.n}", amount=D("20"),
                    reference=f"T-{self.n}")
        data.update(kw)
        return fines.save_fine(Fine(**data))

    def scan(self, plus=0):
        return finereminders.run_fine_scan(self.today + timedelta(days=plus))

    def to(self):
        return sorted(m.to[0] for m in mail.outbox)

    def test_nothing_before_a_fine_is_a_week_old(self):
        self.fine(6)
        self.assertEqual(self.scan()["checked"], 0)
        self.assertEqual(mail.outbox, [])

    def test_a_reminder_at_a_week_and_then_every_week_until_paid(self):
        fine = self.fine(7)
        stats = self.scan()
        self.assertEqual((stats["alerts"], stats["emails"]), (1, 1))
        self.assertEqual(self.to(), ["fin@example.com"])                                  # Finance, not HR or Management
        self.assertIn("1 unpaid traffic fine", mail.outbox[0].subject)
        self.assertEqual(self.scan()["alerts"], 0)                                        # not twice in one week
        self.assertEqual(self.scan(plus=6)["alerts"], 0)                                  # still the same week
        self.assertEqual(self.scan(plus=7)["alerts"], 1)                                  # a week later: again
        self.assertEqual(self.scan(plus=14)["alerts"], 1)
        fines.pay_fine(fine, self.today, D("20"))
        self.assertEqual(self.scan(plus=21)["alerts"], 0)                                 # paid: no more
        self.assertEqual(FineReminderLog.objects.count(), 3)

    def test_a_cancelled_fine_is_never_chased(self):
        fines.void_fine(self.fine(10), "dismissed")
        self.assertEqual(self.scan()["checked"], 0)

    def test_one_email_lists_everything_that_is_due(self):
        self.fine(30, offence="Red light")
        self.fine(9, offence="Parking")
        self.fine(3, offence="Not due yet")
        self.assertEqual(self.scan()["alerts"], 2)
        self.assertEqual(len(mail.outbox), 1)                                             # a digest, not one email per fine
        body = mail.outbox[0].body
        self.assertIn("2 traffic fines are still unpaid (40.000 BHD)", body)
        self.assertLess(body.index("Red light"), body.index("Parking"))                  # oldest first
        self.assertNotIn("Not due yet", body)
        self.assertIn("unpaid for 30 days", body)

    def test_a_newer_fine_joins_a_later_email_without_repeating_the_old_one(self):
        self.fine(7, offence="Old one")
        self.fine(4, offence="Newer one")
        self.scan()
        mail.outbox.clear()
        self.assertEqual(self.scan(plus=3)["alerts"], 1)                                  # the newer fine is now a week old
        self.assertIn("Newer one", mail.outbox[0].body)
        self.assertNotIn("Old one", mail.outbox[0].body)

    def test_the_driver_and_who_pays_are_in_the_email(self):
        ali = self.person("Ali")
        self.fine(8, driver=ali, charged_to_employee=True, offence="Speeding")
        self.scan()
        self.assertIn("driver Ali", mail.outbox[0].body)
        self.assertIn("charged to the driver", mail.outbox[0].body)

    def test_each_company_is_told_only_about_its_own_fines(self):
        other = Vehicle.objects.create(company=self.other_co, plate_number="X1", make="Kia")
        self.fine(8, v=self.v, offence="Ours")
        self.fine(8, v=other, offence="Theirs")
        stats = self.scan()
        self.assertEqual(stats["alerts"], 2)
        self.assertEqual(self.to(), ["fin@example.com"])                                  # the other company has no Finance user
        self.assertIn("Ours", mail.outbox[0].body)
        self.assertNotIn("Theirs", mail.outbox[0].body)
        self.assertEqual(FineReminderLog.objects.get(fine__offence="Theirs").recipients, 0)

    def test_fines_of_sold_vehicles_are_still_chased_and_a_missing_email_is_still_logged(self):
        self.fine(10)
        services.mark_sold(self.v, self.today)
        self.finance.email = ""
        self.finance.save()
        self.assertEqual(self.scan()["alerts"], 1)
        self.assertEqual((mail.outbox, FineReminderLog.objects.get().recipients), ([], 0))
