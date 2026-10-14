"""The Bahrain settlement rules, checked against worked examples. Amounts are by hand, from the SIO and LMRA descriptions."""
from datetime import date
from decimal import Decimal

from django.test import SimpleTestCase

from . import nationality
from . import settlement_rules as r

D = Decimal


class ServiceTests(SimpleTestCase):
    def test_whole_years_and_part_years(self):
        self.assertEqual(r.years_between(date(2020, 1, 1), date(2020, 12, 31)), D(1))
        self.assertEqual(r.years_between(date(2020, 1, 1), date(2022, 12, 31)), D(3))
        self.assertEqual(r.years_between(date(2020, 1, 1), date(2020, 6, 30)), D("0.5"))
        self.assertEqual(r.years_between(date(2020, 1, 1), date(2020, 1, 31)).quantize(D("0.0001")), D("0.0833"))
        self.assertEqual(r.years_between(date(2020, 5, 10), date(2020, 5, 9)), D(0))              # before the start: none

    def test_the_bands_half_a_month_for_three_years_then_a_month(self):
        self.assertEqual(r.months_of_wage(D(0), D(2)), D(1))
        self.assertEqual(r.months_of_wage(D(0), D(3)), D("1.5"))
        self.assertEqual(r.months_of_wage(D(0), D(5)), D("3.5"))                 # 1.5 + 2
        self.assertEqual(r.months_of_wage(D(4), D(6)), D(2))                     # all of it in the later band
        self.assertEqual(r.months_of_wage(D(2), D(4)), D("1.5"))                 # 1 year at half, 1 year at full
        self.assertEqual(r.months_of_wage(D(3), D(3)), D(0))

    def test_the_lmra_example_for_a_short_service(self):
        # 150 BHD wage, 15 months: 75 / 12 x 15 = 93.75 (LMRA wages and indemnity booklet)
        g = r.gratuity(date(2022, 1, 1), date(2023, 3, 31), D("150"))
        self.assertEqual(g["employer"], D("93.750"))
        self.assertEqual(g["sio_estimate"], D("0.000"))                           # all of it before the scheme

    def test_a_long_service_is_split_at_the_scheme_start(self):
        g = r.gratuity(date(2016, 3, 1), date(2026, 3, 31), D("600"))             # 8 years before, 2 years 1 month since
        self.assertEqual((g["years_before_scheme"], g["employer_months"]), (D(8), D("6.5")))
        self.assertEqual(g["employer"], D("3900.000"))                            # (3 x 0.5 + 5 x 1) x 600
        self.assertEqual(g["sio_estimate"], D("1250.000"))                        # 25 months of the later band
        self.assertEqual(g["service_years"].quantize(D("0.01")), D("10.08"))

    def test_the_bands_run_over_the_whole_service_not_each_part_separately(self):
        g = r.gratuity(date(2023, 9, 1), date(2026, 8, 31), D("1000"))            # six months before, 2.5 years since
        self.assertEqual(g["employer"], D("250.000"))                             # 0.5 yr at half a month
        self.assertEqual(g["sio_estimate"], D("1250.000"))                        # 2.5 yrs, still in the first band

    def test_service_wholly_before_or_after_the_scheme(self):
        before = r.gratuity(date(2020, 1, 1), date(2024, 2, 29), D("900"))
        self.assertEqual((before["sio_months"], before["years_since_scheme"]), (D(0), D(0)))
        self.assertEqual(before["employer"], D("2400.000"))                       # (1.5 + 1.1667) x 900
        after = r.gratuity(date(2025, 1, 1), date(2026, 12, 31), D("900"))
        self.assertEqual((after["employer"], after["years_before_scheme"]), (D("0.000"), D(0)))
        self.assertEqual(after["sio_estimate"], D("900.000"))

    def test_the_last_day_before_the_scheme_still_counts_as_before(self):
        g = r.gratuity(date(2024, 2, 1), date(2024, 3, 31), D("1000"))
        self.assertEqual(g["years_before_scheme"].quantize(D("0.0001")), D("0.0833"))     # February 2024: one month


class ContributionTests(SimpleTestCase):
    def test_the_rate_the_company_pays_the_sio(self):
        joined = date(2023, 3, 1)
        self.assertEqual(r.eosg_rate(joined, date(2026, 2, 28)), D("4.2"))        # still inside the first three years
        self.assertEqual(r.eosg_rate(joined, date(2026, 3, 1)), D("8.4"))         # the day the third year ends
        self.assertEqual(r.eosg_rate(date(2016, 5, 1), date(2026, 3, 31)), D("8.4"))


class LeaveAndNoticeTests(SimpleTestCase):
    def test_leave_earned_in_the_year_of_leaving(self):
        self.assertEqual(r.accrued_leave_days(30, date(2020, 1, 1), date(2026, 12, 31)), D(30))
        self.assertEqual(r.accrued_leave_days(30, date(2020, 1, 1), date(2026, 6, 30)), D(15))      # 30 x 181 / 365 = 14.88, to the half day
        self.assertEqual(r.accrued_leave_days(30, date(2020, 1, 1), date(2026, 3, 31)), D("7.5"))
        self.assertEqual(r.accrued_leave_days(30, date(2026, 7, 1), date(2026, 9, 30)), D("7.5"))   # a joiner counts from joining
        self.assertEqual(r.accrued_leave_days(30, date(2026, 12, 1), date(2026, 3, 31)), D(0))
        self.assertEqual(r.accrued_leave_days(30, date(2020, 1, 1), date(2024, 12, 31)), D(30))     # a leap year

    def test_notice_served_and_short(self):
        self.assertEqual(r.notice_shortfall(30, date(2026, 3, 1), date(2026, 3, 31)), (30, 0))
        self.assertEqual(r.notice_shortfall(30, date(2026, 3, 10), date(2026, 3, 31)), (21, 9))
        self.assertEqual(r.notice_shortfall(30, None, date(2026, 3, 31)), (0, 30))
        self.assertEqual(r.notice_shortfall(60, date(2026, 1, 1), date(2026, 3, 31)), (89, 0))


class NationalityTests(SimpleTestCase):
    def test_gcc_nationals_are_told_apart(self):
        for text in ("Saudi", "omani", "Emirati", "Kuwait", "UAE", "Qatari"):
            self.assertTrue(nationality.is_gcc(text), text)
        for text in ("Bahraini", "Indian", "", None, "Egyptian"):
            self.assertFalse(nationality.is_gcc(text), text)
        self.assertEqual(nationality.classify("Omani"), nationality.OTHER)         # still 'not Bahraini' for SIO purposes
