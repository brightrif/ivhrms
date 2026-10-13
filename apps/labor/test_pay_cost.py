"""The cost report for hourly workers, the Friday with no premium, and paid holidays. Rate 1.25 an hour, 8-hour day,
overtime 1.25, premium 1.5. Ravi works Sun-Thu and Sat as normal days (8 h each, Monday 10 h) and, here, a Friday of 8 h."""
from datetime import date
from decimal import Decimal

from apps.attendance.models import Attendance

from . import overtime_services as ot
from . import services, sitesheet
from .overtime import OvertimeClaim
from .test_overtime import Holiday
from .test_reporting import ReportCase
from .test_sitesheet import FRI
from .test_timekeeping import MON, TUE

D = Decimal


class HourlyCostTests(ReportCase):
    def week(self, overtime, premium):
        services.change_rate(self.worker1, effective_from=date(2026, 2, 1), wage_basis="hourly", rate=D("1.25"),
                             standard_hours=D("8"), overtime_eligible=overtime, weekend_premium=premium,
                             paid_holidays=False)
        sitesheet.save_sheet(self.root, self.p1, self.site1, FRI, FRI, {self.key(self.emp, FRI): "present"}, "")
        self.confirmed({self.hkey(self.emp, MON): "10"})
        self.prepare()
        ot.decide(self.root, OvertimeClaim.objects.all(), True)
        row = next(r for r in self.only(self.cost("worker")).rows if r[1] == self.emp.full_name)
        return row[6], row[8], row[9]                       # wages, approved overtime, total

    def test_overtime_yes_and_friday_yes(self):
        # wages: 6 normal days x 8 h x 1.25 = 60.000. Overtime: Monday 3.125 + Friday 8 h x 1.25 x 1.5 = 15.000
        self.assertEqual(self.week(True, True), (D("60.000"), D("18.125"), D("78.125")))

    def test_overtime_no_and_friday_no_pays_every_hour_at_the_normal_rate(self):
        self.assertEqual(self.week(False, False), (D("72.500"), D("0"), D("72.500")))      # 58 h x 1.25

    def test_overtime_no_and_friday_yes(self):
        self.assertEqual(self.week(False, True), (D("62.500"), D("15.000"), D("77.500")))  # Friday only is a premium claim

    def test_overtime_yes_and_friday_no(self):
        # wages: 6 normal days + the Friday, 8 h each = 56 h x 1.25 = 70.000. Overtime: Monday 3.125 only
        self.assertEqual(self.week(True, False), (D("70.000"), D("3.125"), D("73.125")))


class PaidHolidayTests(ReportCase):
    def wages(self):
        return next(r for r in self.only(self.cost("worker")).rows if r[1] == self.emp.full_name)[6]

    def make_holiday_unworked(self):
        Holiday.objects.create(date=TUE, name="Test holiday")
        Attendance.objects.filter(employee=self.emp, date=TUE).delete()          # nobody worked it

    def test_a_holiday_not_worked_is_unpaid_unless_the_worker_has_paid_holidays(self):
        self.make_holiday_unworked()
        self.assertEqual(self.wages(), D("25.000"))                               # 5 worked days x 5
        services.change_rate(self.worker1, effective_from=date(2026, 2, 1), wage_basis="daily", rate=D("5"),
                             standard_hours=D("8"), overtime_eligible=True, paid_holidays=True)
        self.assertEqual(self.wages(), D("30.000"))                               # plus the holiday, one day's wage
