"""The Bahrain rules for a final settlement, as plain functions that touch no database.

Sources (please have the client's legal adviser confirm before go-live): the Labour Law for the Private Sector (Law 36
of 2012), the SIO's end-of-service gratuity pages and the LMRA wages and indemnity booklet.

  * Gratuity is for non-Bahrainis only (Bahrainis are covered by SIO pension). It is worked on the last basic wage plus
    any "social allowance": 15 days' wage for each of the first three years of service, then one month's wage for each
    year after, pro rata for part years.
  * Since 1 March 2024 the SIO pays the gratuity accrued from that date (the employer pays it to the SIO monthly:
    4.2% of wage in the first three years of service, 8.4% after). The employer still pays, directly, the gratuity for
    service up to 29 February 2024. The three-year bands run over the whole service, so the earlier part fills the first
    band first.
  * Accrued annual leave is paid in cash when employment ends.
  * Notice is at least 30 days."""
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from dateutil.relativedelta import relativedelta

ZERO, CENT = Decimal("0"), Decimal("0.001")
SCHEME_START = date(2024, 3, 1)               # the SIO gratuity scheme for non-Bahrainis begins
FIRST_BAND_YEARS = Decimal("3")
FIRST_BAND_MONTHS_PER_YEAR = Decimal("0.5")   # 15 days a year
LATER_BAND_MONTHS_PER_YEAR = Decimal("1")     # a month a year
EOSG_RATE_FIRST, EOSG_RATE_AFTER = Decimal("4.2"), Decimal("8.4")      # % of wage paid monthly to the SIO
DEFAULT_NOTICE_DAYS = 30


def money(value):
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def years_between(start, last_day):
    """Service from `start` to `last_day` (that day included) in years, with part years in months and days."""
    if last_day < start:
        return ZERO
    gap = relativedelta(last_day + timedelta(days=1), start)
    return Decimal(gap.years) + (Decimal(gap.months) + Decimal(gap.days) / Decimal(30)) / Decimal(12)


def months_of_wage(from_year, to_year):
    """Months of wage earned between two points on the service timeline (in years): half a month a year inside the first
    three years, a month a year after."""
    first = max(min(to_year, FIRST_BAND_YEARS) - min(from_year, FIRST_BAND_YEARS), ZERO)
    later = max(max(to_year, FIRST_BAND_YEARS) - max(from_year, FIRST_BAND_YEARS), ZERO)
    return first * FIRST_BAND_MONTHS_PER_YEAR + later * LATER_BAND_MONTHS_PER_YEAR


def gratuity(joining, last_day, wage):
    """Split the gratuity at the SIO scheme's start. `employer` is what the employer pays now (service before
    1 March 2024); `sio_estimate` is what the SIO pays the employee for the service since (an estimate, for information)."""
    total = years_between(joining, last_day)
    before = years_between(joining, min(last_day, SCHEME_START - timedelta(days=1))) if joining < SCHEME_START else ZERO
    employer_months = months_of_wage(ZERO, before)
    sio_months = months_of_wage(before, total)
    return {"service_years": total, "years_before_scheme": before, "years_since_scheme": total - before,
            "employer_months": employer_months, "sio_months": sio_months,
            "employer": money(wage * employer_months), "sio_estimate": money(wage * sio_months)}


def eosg_rate(joining, on_day):
    """The SIO contribution rate (%) the employer pays that month: 4.2 in the first three years of service, 8.4 after."""
    return EOSG_RATE_FIRST if on_day < joining + relativedelta(years=3) else EOSG_RATE_AFTER


def accrued_leave_days(annual_days, joining, last_day):
    """Leave earned in the year of the last working day, from the start of the year (or joining) to that day, in half
    days. The system grants the whole year's entitlement up front; only the earned part is owed."""
    first = max(date(last_day.year, 1, 1), joining)
    days_in_year = (date(last_day.year, 12, 31) - date(last_day.year, 1, 1)).days + 1
    if last_day < first:
        return ZERO
    raw = Decimal(annual_days) * Decimal((last_day - first).days + 1) / Decimal(days_in_year)
    return (raw * 2).quantize(Decimal("1"), rounding=ROUND_HALF_UP) / 2


def notice_shortfall(required_days, notice_date, last_day):
    """(days served, days short of the notice required). With no notice date nothing was served."""
    served = max((last_day - notice_date).days, 0) if notice_date else 0
    return served, max(int(required_days) - served, 0)
