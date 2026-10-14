"""The direct labor payroll run: what each worker is paid for a finished month.

The pay follows the labor module's own rules, the ones the cost report documents: the wages of daily, hourly and
monthly workers from their dated pay terms, the Friday and holiday premium (paid through the overtime claims, so the
day is not also paid as a normal day), and the paid holidays and leave, plus the overtime claims that Management has
approved. The claims themselves are read as they are; they are never priced again.

One rule differs on purpose. The cost report pays a monthly salary for every calendar day, so a 31-day month costs
31/30 of the salary. That is fine for an estimate and wrong for pay, so here a monthly worker earns the salary
divided by the days in the month for each day employed, and an absent or unpaid day costs the salary divided by the
company's "days in a month", exactly as staff are paid.

What payroll adds is certainty. A worker's line is blocked, never guessed, whenever the numbers behind it are not
settled: hours still draft or missing, overtime not yet claimed or still waiting for approval, days with no attendance
for a worker who is paid by the day or hour, pay terms missing, a monthly salary on days with no site, or a nationality
payroll cannot read. SIO is taken from Bahraini workers on the wages earned, leaving overtime out (please confirm with
the accountant)."""
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from apps.attendance.models import Attendance
from apps.employees.models import Employee
from apps.labor import overtime_services as overtime
from apps.labor.models import LaborRate
from apps.labor.overtime import OvertimeClaim, OvertimePolicy
from apps.labor.timesheet import TimeEntry
from apps.scheduling.services import HOLIDAY, WEEKLY_OFF, WORKING, day_types

from . import calculation, nationality
from .calculation import issue, money
from .settlement_rules import SCHEME_START, eosg_rate

ZERO, ONE, HUNDRED = Decimal("0"), Decimal("1"), Decimal("100")
Basis = LaborRate.WageBasis
Status = Attendance.Status
FRACTION = {k.value: Decimal(str(v)) for k, v in Attendance.DAY_FRACTION.items()}
OFF = (HOLIDAY, WEEKLY_OFF)


def _settled(run):
    """People whose final settlement pays this month (and later): they must not be in the run as well."""
    from . import settlements
    return settlements.settled_employee_ids(run.company_id, run.period_end)


def labor_people(run):
    """Direct labor who had joined by the end of the month and have not left. Contractor-supplied labor is paid through
    the contractor statement, so it is never here."""
    return list(Employee.objects.filter(company=run.company_id, worker_type=Employee.WorkerType.LABOR,
                                        joining_date__lte=run.period_end, labor_profile__engagement="direct")
                .exclude(status=Employee.Status.SEPARATED).exclude(pk__in=_settled(run)).select_related("labor_profile")
                .order_by("employee_no", "first_name"))


def without_profile(run):
    """Labor workers with no labor profile, so no pay terms: payroll cannot pay them until a profile is added."""
    return list(Employee.objects.filter(company=run.company_id, worker_type=Employee.WorkerType.LABOR,
                                        joining_date__lte=run.period_end, labor_profile__isnull=True)
                .exclude(status=Employee.Status.SEPARATED).order_by("employee_no"))


@dataclass
class Inputs:
    """Everything the lines are worked out from, loaded once for the whole run."""
    rates: dict
    attendance: dict
    entries: dict
    claimed: set
    approved: dict
    pending: Counter
    policies: dict
    kinds: dict


def _rate_on(rates, day):
    return next((r for r in rates if r.effective_from <= day and (r.effective_to is None or day <= r.effective_to)), None)


def _policy_on(policies, company_id, day):
    return next((p for p in policies.get(company_id, ()) if p.effective_from <= day
                 and (p.effective_to is None or day <= p.effective_to)), None)


def gather(run, people):
    first, last = run.period_start, run.period_end
    ids = [e.pk for e in people]
    rates = defaultdict(list)
    for r in LaborRate.objects.filter(profile__employee_id__in=ids).select_related("profile"):
        rates[r.profile.employee_id].append(r)
    attendance = defaultdict(dict)
    for emp_id, day, status in Attendance.objects.filter(employee_id__in=ids, date__range=(first, last)).values_list(
            "employee_id", "date", "status"):
        attendance[emp_id][day] = status
    entries = defaultdict(list)
    for e in TimeEntry.objects.filter(employee_id__in=ids, date__range=(first, last)):
        entries[e.employee_id].append(e)
    claims = list(OvertimeClaim.objects.filter(employee_id__in=ids, date__range=(first, last)))
    claimed = {c.entry_id for c in claims}
    approved = defaultdict(lambda: {"hours": ZERO, "amount": ZERO})
    for c in claims:
        if c.status == OvertimeClaim.Status.APPROVED:
            approved[c.employee_id]["hours"] += c.hours
            approved[c.employee_id]["amount"] += c.amount
    pending = Counter(c.employee_id for c in claims if c.status == OvertimeClaim.Status.PENDING)
    policies = defaultdict(list)
    for p in OvertimePolicy.objects.filter(company_id=run.company_id):
        policies[p.company_id].append(p)
    kinds = {e.pk: day_types(e, first, last) for e in people}
    return Inputs(rates, attendance, entries, claimed, approved, pending, policies, kinds)


def worker_wages(emp, inputs, first, last, month_days=None):
    """(wages, details) for one worker: what the pay terms earn, apart from the overtime claims."""
    rates, attendance = inputs.rates.get(emp.pk, []), inputs.attendance.get(emp.pk, {})
    entries = {}
    for e in inputs.entries.get(emp.pk, []):
        entries.setdefault(e.date, []).append(e)
    kinds = inputs.kinds[emp.pk]
    n_days = month_days or ((last - first).days + 1)         # a part month still earns by the whole month
    parts = {"daily": ZERO, "hourly": ZERO, "monthly": ZERO, "holiday": ZERO}
    day = max(first, emp.joining_date)
    while day <= last:
        rate, status = _rate_on(rates, day), attendance.get(day)
        kind, worked = kinds.get(day, WORKING), status in Attendance.WORKED
        if rate is not None:
            policy = _policy_on(inputs.policies, emp.company_id, day)
            applies = policy is not None and policy.overtime_applies
            if rate.wage_basis == Basis.DAILY and worked:
                through_claim = (kind in OFF and applies and policy.all_hours_on_days_off and rate.weekend_premium)
                if not through_claim:
                    parts["daily"] += rate.rate * FRACTION.get(status, ONE)
            elif rate.wage_basis == Basis.HOURLY:
                for e in entries.get(day, ()):
                    paid_by_claim = ZERO
                    if applies:
                        paid_by_claim = overtime.claim_terms(e, overtime.KINDS.get(kind, OvertimeClaim.DayKind.WORKING),
                                                             policy, rate)[0]
                    parts["hourly"] += rate.rate * (e.hours - paid_by_claim)
            elif rate.wage_basis == Basis.MONTHLY:
                divisor = Decimal(policy.monthly_divisor if policy else 30)
                parts["monthly"] += rate.rate / n_days
                if status in (Status.ABSENT, Status.UNPAID_LEAVE):
                    parts["monthly"] -= rate.rate / divisor
            if rate.paid_holidays and rate.wage_basis != Basis.MONTHLY:
                if status == Status.PAID_LEAVE or (kind == HOLIDAY and not worked):
                    parts["holiday"] += rate.rate if rate.wage_basis == Basis.DAILY else rate.rate * rate.standard_hours
        day += timedelta(days=1)
    wages = max(sum(parts.values(), ZERO), ZERO)
    return wages, parts


def _needs_hours(rate):
    """Hours decide this worker's pay, either as the wage itself or as overtime and premium."""
    return rate is not None and (rate.wage_basis == Basis.HOURLY or rate.overtime_eligible or rate.weekend_premium)


def _plural(n, one, many):
    return f"{n} {one if n == 1 else many}"


def _needs_claim(entry, kind, rate, policies, company_id):
    """True if this confirmed entry should have an overtime or premium claim but has none."""
    claim_kind = overtime.KINDS.get(kind, OvertimeClaim.DayKind.WORKING)
    if not overtime._could_claim(entry, claim_kind, rate):
        return False
    policy = _policy_on(policies, company_id, entry.date)
    if policy is None:
        return True                                            # nobody has decided about overtime: it cannot be paid yet
    if not policy.overtime_applies:
        return False
    return overtime.claim_terms(entry, claim_kind, policy, rate)[0] > 0


def worker_issues(emp, inputs, first, last):
    """The blocking problems and warnings for one worker. Pure reading: nothing is changed."""
    rates, attendance = inputs.rates.get(emp.pk, []), inputs.attendance.get(emp.pk, {})
    entries, kinds = inputs.entries.get(emp.pk, []), inputs.kinds[emp.pk]
    issues = []
    if not rates:
        return [issue("no_terms", "block", "This worker has no pay terms. Set them under Labor first.")]

    no_terms = [d for d, s in attendance.items() if s in Attendance.WORKED and _rate_on(rates, d) is None]
    if no_terms:
        issues.append(issue("no_terms", "block", f"No pay terms on {_plural(len(no_terms), 'day', 'days')} worked, "
                                                 f"the first {min(no_terms):%d %b %Y}."))
    drafts = [e for e in entries if e.status == TimeEntry.Status.DRAFT]
    if drafts:
        issues.append(issue("hours_draft", "block", f"{_plural(len(drafts), 'day of hours is', 'days of hours are')} "
                                                    "still draft. Confirm the timesheet first."))
    with_hours = {e.date for e in entries}
    conflicts = [d for d in with_hours if attendance.get(d) not in Attendance.WORKED]
    if conflicts:
        issues.append(issue("hours_conflict", "block", f"{_plural(len(conflicts), 'day has', 'days have')} hours but "
                                                       "attendance does not say the worker worked. Fix one of them."))
    missing = [d for d, s in attendance.items() if s in Attendance.WORKED and d not in with_hours
               and _needs_hours(_rate_on(rates, d))]
    if missing:
        issues.append(issue("hours_missing", "block", f"{_plural(len(missing), 'worked day has', 'worked days have')} no "
                                                      "hours, and this worker's pay depends on hours."))
    unclaimed = [e for e in entries if e.status == TimeEntry.Status.CONFIRMED and e.pk not in inputs.claimed
                 and _needs_claim(e, kinds.get(e.date), _rate_on(rates, e.date), inputs.policies, emp.company_id)]
    if unclaimed:
        issues.append(issue("overtime_unprepared", "block", f"{_plural(len(unclaimed), 'day has', 'days have')} overtime "
                                                            "or premium hours with no overtime claim. Prepare the claims."))
    if inputs.pending.get(emp.pk):
        issues.append(issue("overtime_pending", "block", f"{_plural(inputs.pending[emp.pk], 'overtime claim is', 'overtime claims are')} "
                                                         "still waiting for approval."))

    start = max(first, emp.joining_date)
    absent_by_basis, absent_monthly = [], []
    day = start
    while day <= last:
        rate = _rate_on(rates, day)
        if day not in attendance and kinds.get(day, WORKING) == WORKING and rate is not None:
            (absent_monthly if rate.wage_basis == Basis.MONTHLY else absent_by_basis).append(day)
        day += timedelta(days=1)
    if absent_by_basis:
        issues.append(issue("attendance_missing", "block", f"{_plural(len(absent_by_basis), 'working day has', 'working days have')} "
                                                           "no attendance entry, and this worker is paid only for days worked. "
                                                           "Mark each day present or absent on the site sheet."))
    if absent_monthly:
        issues.append(issue("attendance_missing_monthly", "warn", f"{_plural(len(absent_monthly), 'working day has', 'working days have')} "
                                                                  "no attendance entry. A monthly salary pays them in full."))
    return issues


def _sio(kind, wages, policy):
    """(sio wage, employee share, employer share, issues). Wages exclude overtime. Never guessed."""
    employee_rate, employer_rate = (calculation._decimal(policy.get("sio_employee_rate")),
                                    calculation._decimal(policy.get("sio_employer_rate")))
    other_rate = calculation._decimal(policy.get("sio_employer_rate_other"))
    if kind == nationality.UNKNOWN:
        return ZERO, ZERO, ZERO, [issue("nationality", "block", "The nationality is blank or not recognised, so SIO "
                                                               "cannot be decided. Correct it on the worker's record.")]
    if wages <= 0:
        return ZERO, ZERO, ZERO, []
    floor, cap = calculation._decimal(policy.get("sio_wage_floor")), calculation._decimal(policy.get("sio_wage_cap"))
    wage = wages
    if cap is not None and wage > cap:
        wage = cap
    elif floor is not None and wage < floor:
        wage = floor
    wage = money(wage)
    if kind == nationality.BAHRAINI:
        if employee_rate is None or employer_rate is None:
            return ZERO, ZERO, ZERO, [issue("sio_rates", "block", "This worker is Bahraini but the SIO rates are not set "
                                                                   "in the payroll policy. Ask the accountant, then set them.")]
        return wage, money(wage * employee_rate / HUNDRED), money(wage * employer_rate / HUNDRED), []
    if other_rate is not None:
        return wage, ZERO, money(wage * other_rate / HUNDRED), []
    return ZERO, ZERO, ZERO, []


def build_line(emp, inputs, run, payment):
    """The line's fields for one direct labor worker."""
    first, last = run.period_start, run.period_end
    wages_raw, parts = worker_wages(emp, inputs, first, last, getattr(run, "month_days", None))
    approved = inputs.approved.get(emp.pk) or {"hours": ZERO, "amount": ZERO}
    wages, overtime_pay = money(wages_raw), money(approved["amount"])
    issues = worker_issues(emp, inputs, first, last)
    if wages + overtime_pay == 0 and not any(i["level"] == "block" for i in issues):
        issues.append(issue("no_pay", "warn", "Nothing is payable this month: no days worked and no hours."))
    kind = nationality.classify(emp.nationality)
    sio_wage, sio_employee, sio_employer, sio_issues = _sio(kind, wages, run.policy_values)
    issues += sio_issues
    eosg = None                       # the company's monthly payment to the SIO for a non-Bahraini's end-of-service gratuity
    if kind == nationality.OTHER and not nationality.is_gcc(emp.nationality) and wages > 0 and last >= SCHEME_START:
        rate = eosg_rate(emp.joining_date, last)
        eosg = {"rate": str(rate), "wage": str(wages), "amount": str(money(wages * rate / HUNDRED))}
    method, bank, iban = payment if payment else ("", "", "")
    if not method:
        issues.append(issue("payment", "block", "No payment details. Say whether this person is paid by bank or cash."))

    attendance = inputs.attendance.get(emp.pk, {})
    worked = sum(1 for s in attendance.values() if s in Attendance.WORKED)
    lost = [(d, s, ONE - FRACTION.get(s, ONE)) for d, s in sorted(attendance.items()) if FRACTION.get(s, ONE) < 1]
    rates = inputs.rates.get(emp.pk, [])
    basis = sorted({r.wage_basis for r in rates if r.effective_from <= last and (r.effective_to is None or r.effective_to >= first)})
    return {
        "nationality_kind": kind, "days_employed": worked, "gross": money(wages + overtime_pay),
        "unpaid_days": sum((x[2] for x in lost), ZERO), "absence_deduction": ZERO, "sio_wage": sio_wage,
        "sio_employee": sio_employee, "sio_employer": sio_employer, "payment_method": method, "bank_name": bank,
        "iban": iban, "issues": issues,
        "breakdown": {
            "labor": True, "basis": basis, "days_in_period": (last - first).days + 1,
            "days_worked": str(sum((FRACTION.get(a, ONE) for a in attendance.values() if a in Attendance.WORKED), ZERO)),
            "hours": str(sum((e.hours for e in inputs.entries.get(emp.pk, [])), ZERO)), "wages": str(wages),
            "wages_by_kind": {k: str(money(v)) for k, v in parts.items() if v},
            "overtime_hours": str(approved["hours"]), "overtime": str(overtime_pay),
            "segments": [], "missing_days": [], "eosg": eosg,
            "lost_days": [{"date": d.isoformat(), "status": s, "lost": str(x), "amount": ""} for d, s, x in lost]},
    }
