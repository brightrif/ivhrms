"""Pay switches on labor rates: hourly pay basis, overtime, Friday/holiday premium, paid holidays."""
from datetime import date
from decimal import Decimal as D
from types import SimpleNamespace

from . import services
from . import tests as base
from .models import LaborRate
from .overtime_services import hourly_rate


class PaySwitchTests(base.LaborCase):
    def test_callers_that_pass_no_switches_keep_the_old_behaviour(self):
        rate = self.make_worker().rates.get(effective_to__isnull=True)
        self.assertEqual(rate.weekend_premium, rate.overtime_eligible)     # premium follows overtime
        self.assertFalse(rate.paid_holidays)                               # holidays unpaid

    def test_the_three_switches_are_saved_independently(self):
        p = self.make_worker()
        services.change_rate(p, effective_from=date(2030, 1, 1), wage_basis="hourly", rate=D("1.25"),
                             standard_hours=D("8"), overtime_eligible=False, weekend_premium=True,
                             paid_holidays=True)
        r = p.rates.get(effective_to__isnull=True)
        self.assertEqual((r.wage_basis, r.overtime_eligible, r.weekend_premium, r.paid_holidays),
                         ("hourly", False, True, True))

    def test_an_hourly_rate_is_not_divided_by_the_standard_hours(self):
        rate = LaborRate(wage_basis="hourly", rate=D("1.25"), standard_hours=D("8"))
        self.assertEqual(hourly_rate(rate, SimpleNamespace(monthly_divisor=30)), D("1.2500"))

    def test_daily_and_monthly_rates_still_work_out_the_same(self):
        policy = SimpleNamespace(monthly_divisor=30)
        daily = LaborRate(wage_basis="daily", rate=D("10"), standard_hours=D("8"))
        monthly = LaborRate(wage_basis="monthly", rate=D("300"), standard_hours=D("8"))
        self.assertEqual(hourly_rate(daily, policy), D("1.2500"))
        self.assertEqual(hourly_rate(monthly, policy), D("1.2500"))

    def test_a_contracted_worker_may_be_paid_hourly_but_not_monthly(self):
        p = self.make_worker(engagement="contracted", contractor=self.contractor, wage_basis="hourly", rate=D("1.25"))
        with self.assertRaisesMessage(services.LaborError, "not a monthly salary"):
            services.change_rate(p, effective_from=date(2030, 1, 1), wage_basis="monthly", rate=D("300"),
                                 standard_hours=D("8"), overtime_eligible=True)

    def test_a_contractor_starts_with_no_default_switches(self):
        self.assertIsNone(self.contractor.default_overtime)
        self.assertIsNone(self.contractor.default_weekend_premium)
