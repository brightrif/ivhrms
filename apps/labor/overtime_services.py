"""Overtime rules and claims. Views and forms call these; they never change the overtime tables directly.

Flow: HR sets the company's rules once, then for a site and period HR prepares claims from the confirmed hours.
Management approves or rejects them. Approved claims are what payroll will read."""
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.utils import timezone

from apps.employees.models import Employee
from apps.scheduling.services import HOLIDAY, WEEKLY_OFF, day_types

from .models import LaborRate
from .overtime import OvertimeClaim, OvertimePolicy
from .services import LaborError
from .timesheet import TimeEntry

ZERO = Decimal("0")
Claim = OvertimeClaim
KINDS = {HOLIDAY: Claim.DayKind.HOLIDAY, WEEKLY_OFF: Claim.DayKind.WEEKLY_OFF}


def policy_on(company, on_date):
    """The overtime rules in force for a company on a date, or None if none were ever set."""
    return (OvertimePolicy.objects.filter(company=company, effective_from__lte=on_date)
            .exclude(effective_to__lt=on_date).order_by("-effective_from").first())


def overtime_mode(company, on_date):
    """What a company has decided about overtime on a date: ("applies" | "none" | "undecided", the policy or None).
    "undecided" means nobody has set rules yet, which is different from deciding that there is no overtime."""
    policy = policy_on(company, on_date)
    if policy is None:
        return "undecided", None
    return ("applies" if policy.overtime_applies else "none"), policy


@transaction.atomic
def set_policy(company, *, effective_from, working_day_multiplier, weekly_off_multiplier, holiday_multiplier,
               all_hours_on_days_off, monthly_divisor, overtime_applies=True):
    """Start new overtime rules on a date (or, with overtime_applies=False, record that the company pays no overtime
    from then on). The old rules end the day before, and claims already made keep the rules they were worked out
    under. Draft timesheet hours from that date on are brought in line; confirmed hours are history and stay as they were."""
    for label, value in (("Normal working day", working_day_multiplier), ("Weekly off day", weekly_off_multiplier),
                         ("Public holiday", holiday_multiplier)):
        if not Decimal("1") <= Decimal(value) <= Decimal("5"):
            raise LaborError(f"{label}: the multiplier must be between 1 and 5 times the hourly rate.")
    if not 20 <= int(monthly_divisor) <= 31:
        raise LaborError("Days in a month must be between 20 and 31.")
    current = OvertimePolicy.objects.select_for_update().filter(company=company, effective_to__isnull=True).first()
    if current:
        if effective_from <= current.effective_from:
            raise LaborError(f"The new rules must start after the current ones, which began on "
                             f"{current.effective_from:%d %b %Y}.")
        current.effective_to = effective_from - timedelta(days=1)
        current.save()
    policy = OvertimePolicy.objects.create(
        company=company, effective_from=effective_from, overtime_applies=overtime_applies,
        working_day_multiplier=working_day_multiplier, weekly_off_multiplier=weekly_off_multiplier,
        holiday_multiplier=holiday_multiplier, all_hours_on_days_off=all_hours_on_days_off,
        monthly_divisor=monthly_divisor)
    realign_drafts(company, effective_from)
    return policy


def realign_drafts(company, from_date):
    """Draft hours from a date on take overtime eligibility from the worker's own setting AND the company's rules,
    so switching overtime off (or on) shows correctly in timesheets already started. Returns how many changed."""
    rates, changed = {}, 0
    drafts = (TimeEntry.objects.filter(employee__company=company, date__gte=from_date, status=TimeEntry.Status.DRAFT)
              .select_related("employee"))
    for entry in drafts:
        if entry.employee_id not in rates:
            rates[entry.employee_id] = list(LaborRate.objects.filter(profile__employee_id=entry.employee_id))
        rate = _rate_on(rates[entry.employee_id], entry.date)
        mode, _ = overtime_mode(company, entry.date)
        eligible = bool(rate and rate.overtime_eligible and mode != "none")
        if eligible != entry.overtime_eligible:
            entry.overtime_eligible = eligible
            entry.save(update_fields=["overtime_eligible"])
            changed += 1
    return changed


def _could_claim(entry, kind, rate):
    """Quick check, before any rules are looked up: can this entry possibly produce a claim?
    A worker's Friday and holiday premium counts even when the worker has no overtime."""
    premium = entry.overtime_eligible if rate is None else rate.weekend_premium
    if kind == Claim.DayKind.WORKING:
        return entry.overtime_eligible and entry.hours > entry.expected_hours
    return entry.overtime_eligible or premium


def claim_terms(entry, kind, policy, rate=None):
    """(hours, multiplier) to claim for one entry. The worker's own switches decide whether they are on the
    company's multipliers at all; the multipliers themselves stay in the company's overtime rules.
    With no rate given, the premium follows the overtime flag (the old behaviour)."""
    overtime = entry.overtime_eligible
    premium = overtime if rate is None else rate.weekend_premium
    beyond = max(entry.hours - entry.expected_hours, ZERO)
    if kind == Claim.DayKind.WORKING:
        return (beyond if overtime else ZERO), policy.working_day_multiplier
    if premium:                                       # Friday or holiday at the premium multiplier
        return (entry.hours if policy.all_hours_on_days_off else beyond), policy.multiplier_for(kind)
    if overtime:                                      # overtime but no premium: beyond the day, at the normal multiplier
        return beyond, policy.working_day_multiplier
    return ZERO, policy.working_day_multiplier


def overtime_hours(entry, kind, policy, rate=None):
    """The hours of this entry that are paid as overtime."""
    return claim_terms(entry, kind, policy, rate)[0]


def hourly_rate(rate, policy):
    """The worker's pay for one hour. A daily wage is spread over the standard hours; a monthly salary over the
    days in a month first."""
    if rate.wage_basis == LaborRate.WageBasis.HOURLY:         # an hourly rate already is the hour
        return rate.rate.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
    per_day = rate.rate if rate.wage_basis == LaborRate.WageBasis.DAILY else rate.rate / policy.monthly_divisor
    return (per_day / rate.standard_hours).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def _rate_on(rates, day):
    return next((r for r in rates if r.effective_from <= day and (r.effective_to is None or day <= r.effective_to)), None)


@transaction.atomic
def prepare_claims(user, project, location, first, last):
    """Work out overtime claims for the confirmed hours at a site in a period that have none yet.
    Returns (created, skipped) where skipped is a list of texts to show."""
    entries = list(TimeEntry.objects.for_user(user).filter(
        project=project, location=location, date__range=(first, last), status=TimeEntry.Status.CONFIRMED,
        overtime_claim__isnull=True).select_related("employee", "employee__labor_profile").order_by("employee_id", "date"))
    if not entries:
        raise LaborError("There are no confirmed hours without a claim in this period. Confirm the timesheet first.")
    kinds, rates, created, skipped = {}, {}, 0, []
    for e in entries:
        label = f"{e.employee.employee_no} {e.date:%d %b}"
        if e.employee_id not in kinds:
            kinds[e.employee_id] = day_types(e.employee, first, last)
            rates[e.employee_id] = list(e.employee.labor_profile.rates.all())
        kind = KINDS.get(kinds[e.employee_id][e.date], Claim.DayKind.WORKING)
        rate = _rate_on(rates[e.employee_id], e.date)
        if not _could_claim(e, kind, rate):
            continue                                  # no overtime is possible here, whatever the rules say
        policy = policy_on(e.employee.company, e.date)
        if policy is None:
            raise LaborError(f"{e.employee.company.name} has no overtime decision for {e.date:%d %b %Y}. "
                             "Set it under Overtime > Rules first.")
        if not policy.overtime_applies:
            continue                                  # the company has decided it pays no overtime
        hours, multiplier = claim_terms(e, kind, policy, rate)
        if hours <= 0:
            continue
        if rate is None:
            skipped.append(f"{label}: no pay terms on that date")
            continue
        hourly = hourly_rate(rate, policy)
        Claim.objects.create(
            entry=e, employee=e.employee, date=e.date, project=e.project, location=e.location, work_order=e.work_order,
            day_kind=kind, hours=hours, hourly_rate=hourly, multiplier=multiplier, policy=policy,
            amount=(hours * hourly * multiplier).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP))
        created += 1
    return created, skipped


@transaction.atomic
def decide(user, claims, approve, note=""):
    """Approve or reject pending claims. A decision is final: decided claims are left alone and counted as skipped.
    Rejecting needs a reason. Returns (decided, skipped)."""
    note = (note or "").strip()
    if not approve and not note:
        raise LaborError("Give a reason when rejecting overtime.")
    decided = skipped = 0
    for claim in claims.select_for_update():
        if claim.status != Claim.Status.PENDING:
            skipped += 1
            continue
        claim.status = Claim.Status.APPROVED if approve else Claim.Status.REJECTED
        claim.decided_by, claim.decided_at, claim.decision_note = user, timezone.now(), note[:255]
        claim.save()
        decided += 1
    return decided, skipped


@transaction.atomic
def void_claims(user, project, location, first, last):
    """Delete every claim in a period so the hours can be reopened and corrected. For a person with the delete
    permission only; the audit log keeps what was removed."""
    claims = list(Claim.objects.for_user(user).filter(project=project, location=location, date__range=(first, last)))
    if not claims:
        raise LaborError("There are no overtime claims in this period.")
    for c in claims:
        c.delete()
    return len(claims)


def summary(claims):
    """Hours and amount by status for a queryset of claims."""
    out = {s: {"count": 0, "hours": ZERO, "amount": ZERO} for s in Claim.Status.values}
    for c in claims:
        row = out[c.status]
        row["count"] += 1
        row["hours"] += c.hours
        row["amount"] += c.amount
    return out
