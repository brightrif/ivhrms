"""The pay rules, tested with plain numbers. The SIO rates used here are made up for the tests; they are not the
legal rates."""
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

from django.test import SimpleTestCase

from apps.scheduling.services import WEEKLY_OFF, WORKING

from . import calculation as calc

D = Decimal
PAY = ("bank", "NBB", "BH67BMAG00001299123456")


def rec(pk, start, end, total, sio=None):
    return SimpleNamespace(pk=pk, effective_from=start, effective_to=end, total=D(total), sio_wage=D(sio if sio is not None else total))


def policy(**over):
    base = {"days_in_month": 30, "sio_employee_rate": "5", "sio_employer_rate": "10", "sio_employer_rate_other": None,
            "sio_wage_floor": None, "sio_wage_cap": None, "sio_on_paid_days": False}
    base.update(over)
    return base


def kinds(first, last):
    """Fridays are the weekly off, like the system default."""
    out, d = {}, first
    while d <= last:
        out[d] = WEEKLY_OFF if d.weekday() == 4 else WORKING
        d += timedelta(days=1)
    return out


def line(first, last, records, *, joined=date(2020, 1, 1), attendance=None, nat="Indian", pol=None, payment=PAY,
         today=date(2030, 1, 1)):
    return calc.compute_line(joining_date=joined, period_start=first, period_end=last,
                             days_in_month=(pol or policy())["days_in_month"], records=records,
                             attendance=attendance or {}, day_kinds=kinds(first, last), today=today,
                             nationality_text=nat, policy=pol or policy(), payment=payment)


def codes(result, level=None):
    return sorted(i["code"] for i in result["issues"] if level in (None, i["level"]))


MAR1, MAR31 = date(2026, 3, 1), date(2026, 3, 31)          # 31 days
APR1, APR30 = date(2026, 4, 1), date(2026, 4, 30)          # 30 days
FEB1, FEB28 = date(2026, 2, 1), date(2026, 2, 28)          # 28 days


def everyone_present(first, last):
    out, d = {}, first
    while d <= last:
        out[d] = "weekly_off" if d.weekday() == 4 else "present"
        d += timedelta(days=1)
    return out


class GrossTests(SimpleTestCase):
    def test_a_full_month_is_exactly_the_monthly_salary_whatever_its_length(self):
        for first, last in ((FEB1, FEB28), (APR1, APR30), (MAR1, MAR31)):
            r = line(first, last, [rec(1, date(2020, 1, 1), None, "650.000")], attendance=everyone_present(first, last))
            self.assertEqual((r["gross"], r["days_employed"]), (D("650.000"), (last - first).days + 1), first)

    def test_someone_who_joins_part_way_is_paid_for_the_days_employed(self):
        r = line(APR1, APR30, [rec(1, date(2026, 4, 11), None, "600")], joined=date(2026, 4, 11),
                 attendance={})
        self.assertEqual((r["days_employed"], r["gross"]), (20, D("400.000")))

    def test_a_raise_part_way_is_paid_day_by_day(self):
        records = [rec(2, date(2026, 3, 16), None, "900"), rec(1, date(2020, 1, 1), date(2026, 3, 15), "600")]
        r = line(MAR1, MAR31, records)
        self.assertEqual(r["gross"], D("754.839"))                       # 600 x 15/31 + 900 x 16/31
        self.assertEqual([(s["days"], s["monthly_total"]) for s in r["breakdown"]["segments"]],
                         [(15, "600"), (16, "900")])

    def test_days_before_the_first_salary_block_the_line(self):
        r = line(MAR1, MAR31, [rec(1, date(2026, 3, 11), None, "620")])
        self.assertEqual(codes(r, "block"), ["no_salary"])
        self.assertEqual(r["gross"], D("420.000"))                       # only the days that have a salary
        self.assertIn("01 Mar 2026 and 9 more day(s)", r["issues"][0]["text"])
        r = line(MAR1, MAR31, [])
        self.assertEqual((r["gross"], codes(r, "block")), (D("0.000"), ["no_salary"]))


class AbsenceTests(SimpleTestCase):
    def test_a_day_lost_costs_one_thirtieth_even_in_a_31_day_month(self):
        att = everyone_present(MAR1, MAR31)
        att[date(2026, 3, 10)] = "absent"
        r = line(MAR1, MAR31, [rec(1, date(2020, 1, 1), None, "900")], attendance=att)
        self.assertEqual((r["absence_deduction"], r["unpaid_days"]), (D("30.000"), D("1")))
        self.assertEqual(r["breakdown"]["lost_days"][0]["amount"], "30.000")

    def test_unpaid_leave_and_half_days_and_paid_leave(self):
        att = everyone_present(MAR1, MAR31)
        att[date(2026, 3, 10)] = "unpaid_leave"
        att[date(2026, 3, 11)] = "half_day"
        att[date(2026, 3, 12)] = "paid_leave"
        att[date(2026, 3, 15)] = "late_entry"
        r = line(MAR1, MAR31, [rec(1, date(2020, 1, 1), None, "900")], attendance=att)
        self.assertEqual((r["absence_deduction"], r["unpaid_days"]), (D("45.000"), D("1.5")))

    def test_a_lost_day_is_priced_at_the_salary_of_that_day(self):
        att = everyone_present(MAR1, MAR31)
        att[date(2026, 3, 20)] = "absent"
        records = [rec(2, date(2026, 3, 16), None, "900"), rec(1, date(2020, 1, 1), date(2026, 3, 15), "600")]
        r = line(MAR1, MAR31, records, attendance=att)
        self.assertEqual(r["absence_deduction"], D("30.000"))

    def test_the_deduction_never_exceeds_the_gross(self):
        att = {d: "absent" for d in kinds(MAR1, MAR31)}                     # 31 days lost at 1/30 each is more than the pay
        r = line(MAR1, MAR31, [rec(1, date(2020, 1, 1), None, "300")], attendance=att)
        self.assertEqual((r["gross"], r["absence_deduction"], r["unpaid_days"]), (D("300.000"), D("300.000"), D("31")))

    def test_the_policy_divisor_is_used(self):
        att = everyone_present(MAR1, MAR31)
        att[date(2026, 3, 10)] = "absent"
        r = line(MAR1, MAR31, [rec(1, date(2020, 1, 1), None, "900")], attendance=att, pol=policy(days_in_month=26))
        self.assertEqual(r["absence_deduction"], D("34.615"))

    def test_working_days_without_an_entry_are_paid_but_flagged(self):
        att = everyone_present(MAR1, MAR31)
        for day in (date(2026, 3, 4), date(2026, 3, 5)):
            del att[day]
        r = line(MAR1, MAR31, [rec(1, date(2020, 1, 1), None, "900")], attendance=att)
        self.assertEqual((r["gross"], r["absence_deduction"]), (D("900.000"), D("0.000")))
        self.assertEqual(codes(r, "warn"), ["no_attendance"])
        self.assertEqual(r["breakdown"]["missing_days"], ["2026-03-04", "2026-03-05"])
        self.assertIn("2 working day", r["issues"][0]["text"])
        del att[date(2026, 3, 6)]                                         # a Friday: weekly off, not missing
        self.assertEqual(len(line(MAR1, MAR31, [rec(1, date(2020, 1, 1), None, "900")],
                                  attendance=att)["breakdown"]["missing_days"]), 2)

    def test_days_that_have_not_happened_yet_are_not_missing(self):
        r = line(MAR1, MAR31, [rec(1, date(2020, 1, 1), None, "900")], attendance={}, today=date(2026, 3, 3))
        self.assertEqual(r["breakdown"]["missing_days"], ["2026-03-01", "2026-03-02", "2026-03-03"])


class SioTests(SimpleTestCase):
    def full(self, **kw):
        return line(MAR1, MAR31, kw.pop("records", [rec(1, date(2020, 1, 1), None, "1000", "800")]),
                    attendance=everyone_present(MAR1, MAR31), **kw)

    def test_a_bahraini_pays_the_employee_share_and_the_company_the_employer_share(self):
        r = self.full(nat="Bahraini")
        self.assertEqual((r["sio_wage"], r["sio_employee"], r["sio_employer"]), (D("800.000"), D("40.000"), D("80.000")))
        self.assertEqual(r["nationality_kind"], "bahraini")

    def test_someone_who_is_not_bahraini_pays_no_sio(self):
        r = self.full(nat="Indian")
        self.assertEqual((r["sio_employee"], r["sio_employer"]), (D("0.000"), D("0.000")))
        r = self.full(nat="Indian", pol=policy(sio_employer_rate_other="3"))
        self.assertEqual((r["sio_employee"], r["sio_employer"], r["sio_wage"]), (D("0.000"), D("24.000"), D("800.000")))

    def test_the_wage_is_kept_between_the_lowest_and_highest(self):
        high = self.full(nat="Bahraini", records=[rec(1, date(2020, 1, 1), None, "6000", "5000")],
                         pol=policy(sio_wage_cap="4000"))
        self.assertEqual((high["sio_wage"], high["sio_employee"]), (D("4000.000"), D("200.000")))
        low = self.full(nat="Bahraini", records=[rec(1, date(2020, 1, 1), None, "300", "200")],
                        pol=policy(sio_wage_floor="400"))
        self.assertEqual(low["sio_wage"], D("400.000"))

    def test_unpaid_days_reduce_the_sio_wage_only_if_the_policy_says_so(self):
        att = everyone_present(MAR1, MAR31)
        att[date(2026, 3, 10)] = "absent"
        records = [rec(1, date(2020, 1, 1), None, "900", "900")]
        kept = line(MAR1, MAR31, records, attendance=att, nat="Bahraini")
        reduced = line(MAR1, MAR31, records, attendance=att, nat="Bahraini", pol=policy(sio_on_paid_days=True))
        self.assertEqual((kept["sio_wage"], reduced["sio_wage"]), (D("900.000"), D("870.000")))

    def test_a_joiner_part_way_pays_sio_on_the_days_employed(self):
        r = line(APR1, APR30, [rec(1, date(2026, 4, 11), None, "600", "600")], joined=date(2026, 4, 11), nat="Bahraini")
        self.assertEqual(r["sio_wage"], D("400.000"))

    def test_sio_is_not_guessed(self):
        r = self.full(nat="Bahraini", pol=policy(sio_employee_rate=None, sio_employer_rate=None))
        self.assertEqual(codes(r, "block"), ["sio_rates"])
        self.assertEqual((r["sio_employee"], r["sio_employer"]), (D("0.000"), D("0.000")))
        for text in ("", "Bahrani", "Atlantis"):
            r = self.full(nat=text)
            self.assertEqual(codes(r, "block"), ["nationality"], text)
            self.assertEqual(r["nationality_kind"], "unknown")

    def test_zero_rates_are_a_decision_not_a_gap(self):
        r = self.full(nat="Bahraini", pol=policy(sio_employee_rate="0", sio_employer_rate="0"))
        self.assertEqual(codes(r, "block"), [])


class PaymentAndNetTests(SimpleTestCase):
    def test_no_payment_details_blocks(self):
        r = line(MAR1, MAR31, [rec(1, date(2020, 1, 1), None, "900")], attendance=everyone_present(MAR1, MAR31),
                 payment=None)
        self.assertEqual(codes(r, "block"), ["payment"])
        self.assertEqual(r["payment_method"], "")

    def test_the_payment_details_are_copied_onto_the_line(self):
        r = line(MAR1, MAR31, [rec(1, date(2020, 1, 1), None, "900")], attendance=everyone_present(MAR1, MAR31))
        self.assertEqual((r["payment_method"], r["bank_name"], r["iban"]), PAY)

    def test_net_is_gross_less_absence_and_employee_sio_plus_and_minus_adjustments(self):
        att = everyone_present(MAR1, MAR31)
        att[date(2026, 3, 10)] = "absent"
        r = line(MAR1, MAR31, [rec(1, date(2020, 1, 1), None, "900")], attendance=att, nat="Bahraini")
        calc.apply_adjustments(r, D("50"), D("20.5"))
        self.assertEqual((r["additions"], r["deductions"]), (D("50.000"), D("20.500")))
        self.assertEqual(r["net"], D("900") - D("30") - D("45") + D("50") - D("20.5"))

    def test_a_negative_net_blocks_until_the_deduction_is_reduced(self):
        r = line(MAR1, MAR31, [rec(1, date(2020, 1, 1), None, "100")], attendance=everyone_present(MAR1, MAR31))
        calc.apply_adjustments(r, D("0"), D("250"))
        self.assertLess(r["net"], 0)
        self.assertIn("negative_net", codes(r, "block"))
        calc.apply_adjustments(r, D("0"), D("20"))
        self.assertNotIn("negative_net", codes(r))
        self.assertEqual(r["net"], D("80.000"))

    def test_money_rounds_half_up_to_the_fils(self):
        self.assertEqual(calc.money(D("1.0005")), D("1.001"))
        self.assertEqual(calc.money(D("1.0004")), D("1.000"))
