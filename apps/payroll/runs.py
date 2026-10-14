"""The payroll run: create it for a month, calculate it, adjust it, send it for approval, and record the payments.

Flow: Draft (HR calculates and adjusts, as often as needed) > Waiting for approval (Management decides in the usual
Approvals inbox) > Approved (frozen: nothing is recalculated again) > Paid (every line has been paid).
A rejected or withdrawn run goes back to Draft. Views call these; they never change the tables directly."""
import calendar
from datetime import date
from decimal import Decimal

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.attendance.models import Attendance
from apps.core import approvals
from apps.core.models import ApprovalAction, ApprovalRequest
from apps.employees.models import Employee
from apps.scheduling.services import day_types

from . import calculation, labor_payroll, settlements
from .models import PaymentDetail, SalaryRecord
from .payrun import PayrollAdjustment, PayrollLine, PayrollRun
from .policy import policy_on
from .services import PayrollError

FLOW_CODE = "payroll.run"
Status, Kind, Category = PayrollRun.Status, PayrollAdjustment.Kind, PayrollAdjustment.Category
ZERO = Decimal("0")
LIVE = [Status.DRAFT, Status.SUBMITTED, Status.APPROVED, Status.PAID]


def month_bounds(year, month):
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


def _locked(run):
    return PayrollRun.objects.select_for_update().select_related("company").get(pk=run.pk)


def _need_draft(run, what):
    if run.status != Status.DRAFT:
        raise PayrollError(f"This run is {run.get_status_display().lower()}, so it cannot be {what}.")


# ------------------------------------------------------------------ creating and calculating

@transaction.atomic
def create_run(company, year, month, *, user, kind=PayrollRun.Kind.STAFF):
    """Start a payroll run for a month and calculate it. A labor run needs the month to be over: hours, attendance and
    overtime are only complete then."""
    if kind not in PayrollRun.Kind.values:
        raise PayrollError("Choose a staff run or a direct labor run.")
    if not 1 <= int(month) <= 12 or not 2000 <= int(year) <= 2100:
        raise PayrollError("Choose a month and a year.")
    start, end = month_bounds(int(year), int(month))
    if start > timezone.localdate():
        raise PayrollError(f"{calendar.month_name[start.month]} {start.year} has not started yet.")
    if kind == PayrollRun.Kind.LABOR and end > timezone.localdate():
        raise PayrollError(f"{calendar.month_name[start.month]} {start.year} is not over yet. A labor run is made once "
                           "the month has ended, when the hours, attendance and overtime are complete.")
    policy = policy_on(company, end)
    if policy is None:
        raise PayrollError(f"{company.name} has no payroll policy for {calendar.month_name[start.month]} {start.year}. "
                           "Set it first (Settings, Payroll).")
    taken = PayrollRun.objects.filter(company=company, kind=kind, year=start.year,
                                      month=start.month).exclude(status=Status.CANCELLED).first()
    if taken:
        raise PayrollError(f"{company.name} already has a {taken.get_kind_display().lower()} payroll run for "
                           f"{taken.label} ({taken.get_status_display().lower()}).")
    run = PayrollRun.objects.create(company=company, kind=kind, year=start.year, month=start.month, period_start=start,
                                    period_end=end, policy=policy, policy_values=policy.values(), prepared_by=user)
    calculate_run(run, user)
    return PayrollRun.objects.get(pk=run.pk)


def staff_in_run(run):
    """The people a staff run covers: staff of the company who had joined by the end of the month and have not left.
    Anyone marked Separated is paid through final settlement instead."""
    settled = settlements.settled_employee_ids(run.company_id, run.period_end)      # their final settlement pays them
    return list(Employee.objects.filter(company=run.company_id, worker_type=Employee.WorkerType.STAFF,
                                        joining_date__lte=run.period_end)
                .exclude(status=Employee.Status.SEPARATED).exclude(pk__in=settled).order_by("employee_no", "first_name"))


def people_in_run(run):
    return labor_payroll.labor_people(run) if run.kind == PayrollRun.Kind.LABOR else staff_in_run(run)


@transaction.atomic
def calculate_run(run, user=None):
    """Work out every line from the pay records, attendance and policy as they are now. Adjustments are kept."""
    run = _locked(run)
    _need_draft(run, "recalculated")
    policy = policy_on(run.company, run.period_end)
    if policy is None:
        raise PayrollError(f"{run.company.name} has no payroll policy for {run.label}.")
    run.policy, run.policy_values = policy, policy.values()
    people = people_in_run(run)
    ids = [p.pk for p in people]
    today = timezone.localdate()

    records = {}
    for r in (SalaryRecord.objects.filter(employee_id__in=ids, effective_from__lte=run.period_end)
              .filter(Q(effective_to__isnull=True) | Q(effective_to__gte=run.period_start))
              .prefetch_related("allowances__allowance_type").order_by("-effective_from")):
        records.setdefault(r.employee_id, []).append(r)
    attendance = {}
    for emp_id, day, status in Attendance.objects.filter(
            employee_id__in=ids, date__range=(run.period_start, run.period_end)).values_list("employee_id", "date", "status"):
        attendance.setdefault(emp_id, {})[day] = status
    payment = {p.employee_id: (p.method, p.bank_name, p.iban) for p in PaymentDetail.objects.filter(employee_id__in=ids)}
    adjusted = {}
    for a in run.adjustments.all():
        pair = adjusted.setdefault(a.employee_id, [ZERO, ZERO])
        pair[0 if a.kind == Kind.ADDITION else 1] += a.amount

    labor = labor_payroll.gather(run, people) if run.kind == PayrollRun.Kind.LABOR else None
    existing = {line.employee_id: line for line in run.lines.all()}
    for emp in people:
        if labor is not None:
            fields = labor_payroll.build_line(emp, labor, run, payment.get(emp.pk))
        else:
            fields = calculation.compute_line(
                joining_date=emp.joining_date, period_start=run.period_start, period_end=run.period_end,
                days_in_month=policy.days_in_month, records=records.get(emp.pk, []),
                attendance=attendance.get(emp.pk, {}), day_kinds=day_types(emp, run.period_start, run.period_end),
                today=today, nationality_text=emp.nationality, policy=run.policy_values, payment=payment.get(emp.pk))
        add, cut = adjusted.get(emp.pk, (ZERO, ZERO))
        calculation.apply_adjustments(fields, add, cut)
        line = existing.pop(emp.pk, None) or PayrollLine(run=run, employee=emp)
        line.employee_no, line.employee_name = emp.employee_no, emp.full_name
        for name, value in fields.items():
            setattr(line, name, value)
        line.save()
    for line in existing.values():                       # no longer covered (left, or moved to another company)
        line.delete()
    run.calculated_at = timezone.now()
    run.note = ""
    run.save()
    return run


# ------------------------------------------------------------------ adjustments

def _refresh_line(line):
    adds = deducts = ZERO
    for a in line.run.adjustments.filter(employee=line.employee):
        if a.kind == Kind.ADDITION:
            adds += a.amount
        else:
            deducts += a.amount
    fields = {"gross": line.gross, "absence_deduction": line.absence_deduction, "sio_employee": line.sio_employee,
              "issues": list(line.issues)}
    calculation.apply_adjustments(fields, adds, deducts)
    line.additions, line.deductions, line.net, line.issues = (fields["additions"], fields["deductions"],
                                                              fields["net"], fields["issues"])
    line.save()
    return line


@transaction.atomic
def add_adjustment(run, employee, *, kind, category, label, amount, source="manual", source_id=None):
    run = _locked(run)
    _need_draft(run, "changed")
    line = run.lines.filter(employee=employee).first()
    if line is None:
        raise PayrollError(f"{employee.full_name} is not in this run.")
    if kind not in Kind.values or category not in Category.values:
        raise PayrollError("Choose whether it is an addition or a deduction, and what for.")
    label = " ".join((label or "").split())
    if not label:
        raise PayrollError("Say what it is for.")
    try:
        amount = Decimal(amount)
    except Exception:
        raise PayrollError("The amount must be a number.")
    if amount <= 0 or amount != calculation.money(amount):
        raise PayrollError("The amount must be more than zero, with at most three decimal places.")
    adjustment = PayrollAdjustment.objects.create(run=run, employee=employee, kind=kind, category=category,
                                                  label=label[:120], amount=amount, source=source, source_id=source_id)
    _refresh_line(line)
    return adjustment


@transaction.atomic
def remove_adjustment(adjustment):
    run = _locked(adjustment.run)
    _need_draft(run, "changed")
    employee = adjustment.employee
    PayrollAdjustment.objects.filter(pk=adjustment.pk).delete()
    line = run.lines.filter(employee=employee).first()
    if line:
        _refresh_line(line)


def charge_candidates(run, user):
    """Costs already marked 'charge to the employee' that nobody has deducted yet, for people in this run:
    renewal payments from Compliance and fines from Vehicles. Returns [(source, id, employee_id, category, label, amount)]."""
    from apps.compliance.models import RenewalPayment
    from apps.vehicles.incidents import Fine

    ids = list(run.lines.values_list("employee_id", flat=True))
    from .finalsettlement import SettlementItem

    taken = set(PayrollAdjustment.objects.filter(source_id__isnull=False).exclude(run__status=Status.CANCELLED)
                .values_list("source", "source_id"))
    taken |= set(SettlementItem.objects.filter(source_id__isnull=False).exclude(settlement__status="cancelled")
                 .values_list("source", "source_id"))
    found = []
    for p in (RenewalPayment.objects.for_user(user)
              .filter(charged_to_employee=True, task__document__employee_id__in=ids, paid_on__lte=run.period_end)
              .select_related("task__document")):
        if ("renewal", p.pk) not in taken and p.total > 0:
            doc = p.task.document
            found.append(("renewal", p.pk, doc.employee_id, Category.RENEWAL,
                          f"{doc.label}: {p.description or 'renewal'}", p.total))
    for f in (Fine.objects.for_user(user)
              .filter(is_voided=False, charged_to_employee=True, recovered_on__isnull=True, driver_id__in=ids,
                      fined_on__lte=run.period_end).select_related("vehicle")):
        if ("fine", f.pk) not in taken and f.display_amount > 0:
            found.append(("fine", f.pk, f.driver_id, Category.FINE,
                          f"Fine {f.reference or f.pk}, {f.offence} ({f.vehicle.plate_number})", f.display_amount))
    return found


@transaction.atomic
def import_charges(run, user):
    """Add the pending charges as deductions. Each can be removed again; each can only ever be deducted once."""
    run = _locked(run)
    _need_draft(run, "changed")
    people = {e.pk: e for e in Employee.objects.filter(pk__in=run.lines.values_list("employee_id", flat=True))}
    added = 0
    for source, source_id, emp_id, category, label, amount in charge_candidates(run, user):
        add_adjustment(run, people[emp_id], kind=Kind.DEDUCTION, category=category, label=label, amount=amount,
                       source=source, source_id=source_id)
        added += 1
    return added


# ------------------------------------------------------------------ approval

def blocking_count(run):
    return sum(1 for line in run.lines.all() if line.blocking)


@transaction.atomic
def submit_run(run, user):
    run = _locked(run)
    _need_draft(run, "sent for approval")
    if not run.lines.exists():
        raise PayrollError("There is nobody in this run.")
    stuck = blocking_count(run)
    if stuck:
        raise PayrollError(f"{stuck} {'person has' if stuck == 1 else 'people have'} a problem to fix first. "
                           "Open the lines marked in red.")
    settled = settlements.settled_employee_ids(run.company_id, run.period_end) & set(run.lines.values_list("employee_id", flat=True))
    if settled:
        raise PayrollError(f"{len(settled)} {'person has' if len(settled) == 1 else 'people have'} a final settlement that pays "
                           "this month, so they must not be in the run. Press Recalculate.")
    employee = getattr(user, "employee", None)
    if employee is None:
        raise PayrollError("Your login is not linked to an employee record, so the approval cannot be sent in your "
                           "name. Ask the system administrator to link it.")
    try:
        approvals.submit(FLOW_CODE, run, employee=employee, requested_by=user)
    except approvals.ApprovalError as exc:
        raise PayrollError(str(exc)) from exc
    run.status = Status.SUBMITTED
    run.note = ""
    run.save()
    return run


def _pending_approval(run):
    from django.contrib.contenttypes.models import ContentType
    return ApprovalRequest.objects.filter(content_type=ContentType.objects.get_for_model(PayrollRun),
                                          object_id=str(run.pk), status=ApprovalRequest.Status.PENDING).first()


@transaction.atomic
def withdraw_run(run, user):
    """Take a run back from Management so it can be changed."""
    run = _locked(run)
    if run.status != Status.SUBMITTED:
        raise PayrollError("This run is not waiting for approval.")
    approval = _pending_approval(run)
    if approval is None:
        raise PayrollError("This run is not waiting for approval.")
    try:
        approvals.cancel(approval.pk, user)
    except approvals.ApprovalError as exc:
        raise PayrollError(str(exc)) from exc
    return PayrollRun.objects.get(pk=run.pk)


@transaction.atomic
def finalize(approval):
    """Called when the approval finishes, whatever the outcome. Idempotent."""
    run = PayrollRun.objects.select_for_update().get(pk=approval.object_id)
    if run.status != Status.SUBMITTED:
        return run
    if approval.status == ApprovalRequest.Status.APPROVED:
        run.status, run.approved_at = Status.APPROVED, timezone.now()
        run.note = _mark_charges_recovered(run)
    else:
        last = approval.actions.filter(decision=ApprovalAction.Decision.REJECT).order_by("-acted_at").first()
        run.status = Status.DRAFT
        run.note = (f"Rejected: {last.comment}" if last and last.comment else
                    "Rejected." if approval.status == ApprovalRequest.Status.REJECTED else "Taken back for changes.")
    run.save()
    return run


def _mark_charges_recovered(run):
    """Once a run is approved, the fines it deducts count as recovered from the driver."""
    from apps.vehicles import recoveries
    from apps.vehicles.incidents import Fine
    from apps.vehicles.services import VehicleError

    problems = []
    for a in run.adjustments.filter(source="fine", source_id__isnull=False):
        fine = Fine.objects.filter(pk=a.source_id).first()
        if fine is None:
            continue
        on = max(fine.fined_on, min(run.period_end, timezone.localdate()))
        try:
            recoveries.mark_recovered(fine, on, f"Deducted in {run.label} payroll")
        except VehicleError as exc:
            problems.append(f"Fine {fine.reference or fine.pk}: {exc}")
    return ("Approved, but check: " + "; ".join(problems))[:500] if problems else ""


@transaction.atomic
def cancel_run(run):
    run = _locked(run)
    _need_draft(run, "cancelled")
    run.status = Status.CANCELLED
    run.save()
    return run


# ------------------------------------------------------------------ payments

def unpaid_lines(run, method):
    return run.lines.filter(payment_method=method, paid_on__isnull=True)


@transaction.atomic
def mark_paid(run, line_ids, paid_on):
    """Record that these people were paid on a date. When everyone has been paid the run becomes Paid."""
    run = _locked(run)
    if run.status != Status.APPROVED:
        raise PayrollError("Payments are recorded only for an approved run that is not fully paid.")
    if paid_on > timezone.localdate():
        raise PayrollError("The payment date cannot be in the future.")
    if paid_on < run.period_start:
        raise PayrollError(f"The payment date cannot be before the month starts ({run.period_start:%d %b %Y}).")
    lines = list(run.lines.select_for_update().filter(pk__in=list(line_ids), paid_on__isnull=True))
    if not lines:
        raise PayrollError("Choose at least one person who has not been paid yet.")
    for line in lines:
        line.paid_on = paid_on
        line.save()
    if not run.lines.filter(paid_on__isnull=True).exists():
        run.status = Status.PAID
        run.save()
    return len(lines)


# ------------------------------------------------------------------ totals for the screens

def totals(run):
    lines = list(run.lines.all())

    def total(name):
        return sum((getattr(line, name) for line in lines), ZERO)

    return {"people": len(lines), "gross": total("gross"), "absence": total("absence_deduction"),
            "sio_employee": total("sio_employee"), "sio_employer": total("sio_employer"),
            "additions": total("additions"), "deductions": total("deductions"), "net": total("net"),
            "blocking": sum(1 for line in lines if line.blocking),
            "warnings": sum(1 for line in lines if line.warnings and not line.blocking),
            "unpaid": sum(1 for line in lines if line.paid_on is None),
            "bank": sum((line.net for line in lines if line.payment_method == "bank"), ZERO),
            "cash": sum((line.net for line in lines if line.payment_method == "cash"), ZERO),
            "eosg": sum((Decimal((line.breakdown.get("eosg") or {}).get("amount", "0")) for line in lines), ZERO)}
