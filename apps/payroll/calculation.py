"""One person's pay for one month. A plain function: it is given the facts and returns the figures, and touches no
database, so every rule here can be tested with ordinary numbers.

The rules, in the words HR will read on the screen:

  * Each day the person is employed earns that day's salary divided by the days in the month. A full month is
    therefore exactly the monthly salary, whatever its length, and a raise or a joining date part-way through is
    paid day by day at the salary in force on each day.
  * Absence, unpaid leave and a half day cost the salary in force that day divided by "days in a month" (30 by
    default) for each day lost, using the paid fractions the attendance module already defines.
  * A working day with no attendance entry is paid in full, and the line is flagged so HR can check it.
  * SIO is taken only from Bahraini staff, on the SIO wage (basic plus the allowances marked as counting), kept
    between the lowest and highest wage in the policy. The company's share is a cost, not a deduction.
  * Anything that stops the line being paid correctly is a blocking issue; the run cannot be sent for approval until
    every blocking issue is fixed."""
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from apps.attendance.models import Attendance
from apps.scheduling.services import WORKING

from . import nationality
from .settlement_rules import SCHEME_START, eosg_rate

ZERO, ONE, CENT = Decimal("0"), Decimal("1"), Decimal("0.001")
HUNDRED = Decimal("100")
FRACTION = {k.value if hasattr(k, "value") else k: Decimal(str(v)) for k, v in Attendance.DAY_FRACTION.items()}


def money(value):
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def issue(code, level, text):
    return {"code": code, "level": level, "text": text}


def _parts(record):
    """The salary's make-up, kept on the line so a payslip can list the basic salary and each allowance."""
    basic = getattr(record, "basic", None)
    if basic is None:
        return None
    return {"basic": str(basic), "allowances": [[a.allowance_type.name, str(a.amount)] for a in record.allowances.all()]}


def record_on(records, day):
    """The salary record in force on a day (records may be in any order), or None."""
    for r in records:
        if r.effective_from <= day and (r.effective_to is None or r.effective_to >= day):
            return r
    return None


def _decimal(value):
    return None if value is None else Decimal(str(value))


def compute_line(*, joining_date, period_start, period_end, days_in_month, records, attendance, day_kinds, today,
                 nationality_text, policy, payment, month_days=None):
    """Returns a dict of the line's fields. `records` are salary records (anything with effective_from, effective_to,
    total and sio_wage); `attendance` is {date: status}; `day_kinds` is {date: 'working' | 'holiday' | 'weekly_off'};
    `policy` is the dict from PayrollPolicy.values(); `payment` is None or (method, bank_name, iban)."""
    n_days = month_days or ((period_end - period_start).days + 1)      # a part month still earns by the whole month
    divisor = Decimal(days_in_month)
    gross = sio_base = absence = sio_lost = unpaid_days = eosg_base = ZERO
    employed = 0
    no_salary, missing, lost_days, segments = [], [], [], []
    last_record = None

    day = period_start
    while day <= period_end:
        if day >= joining_date:
            employed += 1
            record = record_on(records, day)
            if record is None:
                no_salary.append(day)
            else:
                last_record = record
                total, sio = Decimal(record.total), Decimal(record.sio_wage)
                gross += total / n_days
                sio_base += sio / n_days
                eosg_base += Decimal(getattr(record, "gratuity_wage", 0)) / n_days
                if segments and segments[-1]["id"] == record.pk:
                    segments[-1]["to"], segments[-1]["days"] = day, segments[-1]["days"] + 1
                else:
                    segments.append({"id": record.pk, "from": day, "to": day, "days": 1, "total": total, "sio": sio,
                                     "parts": _parts(record)})
                status = attendance.get(day)
                if status is None:
                    if day_kinds.get(day, WORKING) == WORKING and day <= today:
                        missing.append(day)
                else:
                    lost = ONE - FRACTION.get(status, ONE)
                    if lost > 0:
                        cut = total * lost / divisor
                        absence += cut
                        sio_lost += sio * lost / divisor
                        unpaid_days += lost
                        lost_days.append({"date": day.isoformat(), "status": status, "lost": str(lost),
                                          "amount": str(money(cut))})
        day += timedelta(days=1)

    gross = money(gross)
    absence = min(money(absence), gross)
    issues = []
    if no_salary:
        first = f"{no_salary[0]:%d %b %Y}"
        more = f" and {len(no_salary) - 1} more day(s)" if len(no_salary) > 1 else ""
        issues.append(issue("no_salary", "block", f"No salary is on record for {first}{more}. Set the salary first."))
    if missing:
        issues.append(issue("no_attendance", "warn", f"{len(missing)} working day(s) have no attendance entry and "
                                                     "are paid in full. Check them before approving."))

    kind = nationality.classify(nationality_text)
    sio_wage = sio_employee = sio_employer = ZERO
    employee_rate, employer_rate = _decimal(policy.get("sio_employee_rate")), _decimal(policy.get("sio_employer_rate"))
    other_rate = _decimal(policy.get("sio_employer_rate_other"))
    if kind == nationality.UNKNOWN:
        issues.append(issue("nationality", "block", "The nationality is blank or not recognised, so SIO cannot be "
                                                    "decided. Correct it on the staff record."))
    elif gross > 0 and last_record is not None:
        base = sio_base - (sio_lost if policy.get("sio_on_paid_days") else ZERO)
        full = Decimal(last_record.sio_wage)
        floor, cap = _decimal(policy.get("sio_wage_floor")), _decimal(policy.get("sio_wage_cap"))
        factor = ONE
        if full > 0 and cap is not None and full > cap:
            factor = cap / full
        elif full > 0 and floor is not None and full < floor:
            factor = floor / full
        wage = money(max(base, ZERO) * factor)
        if kind == nationality.BAHRAINI:
            if employee_rate is None or employer_rate is None:
                issues.append(issue("sio_rates", "block", "This person is Bahraini but the SIO rates are not set in the "
                                                          "payroll policy. Ask the accountant, then set them."))
            else:
                sio_wage = wage
                sio_employee = money(wage * employee_rate / HUNDRED)
                sio_employer = money(wage * employer_rate / HUNDRED)
        elif other_rate is not None:
            sio_wage = wage
            sio_employer = money(wage * other_rate / HUNDRED)

    eosg = None                       # the company's monthly payment to the SIO for a non-Bahraini's end-of-service gratuity
    if (kind == nationality.OTHER and not nationality.is_gcc(nationality_text) and gross > 0
            and period_end >= SCHEME_START):
        rate = eosg_rate(joining_date, period_end)
        eosg = {"rate": str(rate), "wage": str(money(eosg_base)), "amount": str(money(eosg_base * rate / HUNDRED))}

    method, bank, iban = payment if payment else ("", "", "")
    if not method:
        issues.append(issue("payment", "block", "No payment details. Say whether this person is paid by bank or cash."))

    return {
        "nationality_kind": kind, "days_employed": employed, "gross": gross, "unpaid_days": unpaid_days,
        "absence_deduction": absence, "sio_wage": sio_wage, "sio_employee": sio_employee, "sio_employer": sio_employer,
        "payment_method": method, "bank_name": bank, "iban": iban, "issues": issues,
        "breakdown": {
            "days_in_period": n_days, "days_in_month": days_in_month,
            "segments": [{"from": s["from"].isoformat(), "to": s["to"].isoformat(), "days": s["days"],
                          "monthly_total": str(s["total"]), "amount": str(money(s["total"] * s["days"] / n_days)),
                          "parts": s["parts"]}
                         for s in segments],
            "lost_days": lost_days, "missing_days": [d.isoformat() for d in missing], "eosg": eosg},
    }


def apply_adjustments(fields, additions, deductions):
    """Fill in additions, deductions and net, and keep the 'negative pay' issue in step with them."""
    fields["additions"], fields["deductions"] = money(additions), money(deductions)
    fields["net"] = (fields["gross"] - fields["absence_deduction"] - fields["sio_employee"]
                     + fields["additions"] - fields["deductions"])
    issues = [i for i in fields["issues"] if i["code"] != "negative_net"]
    if fields["net"] < 0:
        issues.append(issue("negative_net", "block", "The deductions are more than the pay, so the net is negative. "
                                                     "Remove or reduce a deduction."))
    fields["issues"] = issues
    return fields
