"""The payroll policy: days in a month and the SIO rules, from a date. Views call these; they never change the table
directly. A policy that has been used by an approved run is history and is never edited, only replaced."""
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Max

from .payrun import PayrollPolicy, PayrollRun
from .services import PayrollError


def policy_on(company, on_date):
    """The policy in force for a company on a date, or None if none was ever set."""
    return (PayrollPolicy.objects.filter(company=company, effective_from__lte=on_date)
            .exclude(effective_to__lt=on_date).order_by("-effective_from").first())


def _rate(value, label):
    if value in (None, ""):
        return None
    try:
        rate = Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        raise PayrollError(f"{label} must be a number.")
    if not Decimal("0") <= rate <= Decimal("100"):
        raise PayrollError(f"{label} must be between 0 and 100.")
    return rate


def _wage(value, label):
    if value in (None, ""):
        return None
    try:
        amount = Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        raise PayrollError(f"{label} must be a number.")
    if amount < 0:
        raise PayrollError(f"{label} cannot be negative.")
    return amount


@transaction.atomic
def set_policy(company, *, effective_from, days_in_month=30, sio_employee_rate=None, sio_employer_rate=None,
               sio_employer_rate_other=None, sio_wage_floor=None, sio_wage_cap=None, sio_on_paid_days=False):
    """Start a new policy on a date. The current one ends the day before. SIO rates may be left empty until the
    accountant gives them; payroll then stops at any Bahraini rather than guess."""
    if not 20 <= int(days_in_month) <= 31:
        raise PayrollError("Days in a month must be between 20 and 31.")
    employee_rate = _rate(sio_employee_rate, "The SIO employee rate")
    employer_rate = _rate(sio_employer_rate, "The SIO employer rate")
    other_rate = _rate(sio_employer_rate_other, "The SIO employer rate for others")
    floor, cap = _wage(sio_wage_floor, "The lowest SIO wage"), _wage(sio_wage_cap, "The highest SIO wage")
    if floor is not None and cap is not None and cap < floor:
        raise PayrollError("The highest SIO wage cannot be below the lowest.")
    frozen = (PayrollRun.objects.filter(company=company, status__in=[PayrollRun.Status.APPROVED, PayrollRun.Status.PAID])
              .aggregate(last=Max("period_end"))["last"])
    if frozen and effective_from <= frozen:
        raise PayrollError(f"Payroll up to {frozen:%d %b %Y} is already approved, so a new policy must start after "
                           "that date.")
    current = PayrollPolicy.objects.select_for_update().filter(company=company, effective_to__isnull=True).first()
    if current:
        if effective_from <= current.effective_from:
            raise PayrollError(f"The new policy must start after the current one, which began on "
                               f"{current.effective_from:%d %b %Y}.")
        current.effective_to = effective_from - timedelta(days=1)
        current.save()
    return PayrollPolicy.objects.create(
        company=company, effective_from=effective_from, days_in_month=int(days_in_month),
        sio_employee_rate=employee_rate, sio_employer_rate=employer_rate, sio_employer_rate_other=other_rate,
        sio_wage_floor=floor, sio_wage_cap=cap, sio_on_paid_days=bool(sio_on_paid_days))
