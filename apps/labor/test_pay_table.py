"""The agreed pay table: a Monday of 10 hours and a Friday (the weekly off) of 8 hours, for each combination of the
worker's Overtime and Friday/holiday premium switches. Rate 5 a day = 0.625 an hour, multipliers 1.25 and 1.5."""
from decimal import Decimal

from . import sitesheet
from .models import LaborRate
from .overtime import OvertimeClaim as Claim
from .test_overtime import OvertimeCase
from .test_sitesheet import FRI
from .test_timekeeping import MON

D = Decimal


class PayTableTests(OvertimeCase):
    def run_week(self, overtime, premium, friday=None):
        LaborRate.objects.filter(profile=self.worker1).update(overtime_eligible=overtime, weekend_premium=premium)
        sitesheet.save_sheet(self.root, self.p1, self.site1, FRI, FRI, {self.key(self.emp, FRI): "present"}, "")
        posted = {self.hkey(self.emp, MON): "10"}
        if friday:
            posted[self.hkey(self.emp, FRI)] = friday
        self.confirmed(posted)
        self.prepare()
        return {c.date: (c.day_kind, c.hours, c.multiplier, c.amount) for c in Claim.objects.all()}

    def test_overtime_yes_and_friday_yes(self):
        self.assertEqual(self.run_week(True, True), {
            MON: ("working", D("2"), D("1.25"), D("1.563")),            # 2 x 0.625 x 1.25
            FRI: ("weekly_off", D("8"), D("1.50"), D("7.500"))})        # every Friday hour at 1.5

    def test_overtime_no_and_friday_no_pays_everything_at_the_normal_rate(self):
        self.assertEqual(self.run_week(False, False), {})               # no claim at all, even after 8 hours

    def test_overtime_no_and_friday_yes_still_gets_the_friday_premium(self):
        self.assertEqual(self.run_week(False, True), {
            FRI: ("weekly_off", D("8"), D("1.50"), D("7.500"))})        # Monday's 10 hours are not overtime

    def test_overtime_yes_and_friday_no_gets_overtime_but_no_premium(self):
        self.assertEqual(self.run_week(True, False), {
            MON: ("working", D("2"), D("1.25"), D("1.563"))})           # Friday's 8 hours are normal hours

    def test_overtime_yes_and_friday_no_hours_beyond_the_day_on_a_friday_are_overtime_at_the_normal_multiplier(self):
        self.assertEqual(self.run_week(True, False, friday="10"), {
            MON: ("working", D("2"), D("1.25"), D("1.563")),
            FRI: ("weekly_off", D("2"), D("1.25"), D("1.563"))})        # 2 x 0.625 x 1.25, not 1.5
