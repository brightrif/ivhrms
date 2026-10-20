"""Final settlements: create the case, calculate it, adjust it, send it for approval, record the payment.

Flow: Draft (HR calculates and adjusts, as often as needed) > Waiting for approval (Management, in the usual Approvals
inbox) > Approved (frozen; the person is marked Separated) > Paid. A rejected or withdrawn case goes back to Draft.
Staff only for now; direct labor follows. Views call these; they never change the tables directly."""
import calendar
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.attendance.models import Attendance
from apps.core import approvals
from apps.core.models import ApprovalAction, ApprovalRequest
from apps.employees.models import Employee
from apps.scheduling.services import day_types

from . import calculation, nationality
from . import settlement_rules as rules
from .calculation import issue, money
from .finalsettlement import FinalSettlement, SettlementItem
from .models import PaymentDetail, SalaryRecord
from .payrun import PayrollLine, PayrollRun
from .policy import policy_on
from .services import PayrollError, salary_on
from . import settlement_labor

FLOW_CODE = "payroll.settlement"
Status, Reason, Kind = FinalSettlement.Status, FinalSettlement.Reason, SettlementItem.Kind
Treatment = FinalSettlement.NoticeTreatment
ZERO = Decimal("0")
NEEDS_NOTICE = (Reason.RESIGNATION, Reason.TERMINATION)


def _locked(s):
    return FinalSettlement.objects.select_for_update().select_related("employee", "company", "leave_type").get(pk=s.pk)


def _need_draft(s, what):
    if s.status != Status.DRAFT:
        raise PayrollError(f"This settlement is {s.get_status_display().lower()}, so it cannot be {what}.")


def settled_employee_ids(company_id, period_end):
    """People a payroll run for a month ending on `period_end` must leave out: their settlement pays their last month
    (and everything after), so including them would pay the same days twice."""
    return set(FinalSettlement.objects.filter(company_id=company_id, last_working_day__lte=period_end)
               .exclude(status=Status.CANCELLED).values_list("employee_id", flat=True))


def default_leave_type(company):
    """The annual leave type: the only tracked paid type, or the one called annual. None if it cannot be told."""
    from apps.leave.models import LeaveType

    types = list(LeaveType.objects.filter(company=company, is_active=True, tracks_balance=True, is_paid=True,
                                          annual_entitlement__gt=0))
    annual = [t for t in types if "annual" in t.name.lower() or t.code.lower() in ("al", "annual")]
    if len(annual) == 1:
        return annual[0]
    return types[0] if len(types) == 1 else None


# ------------------------------------------------------------------ the case

def _check_inputs(employee, *, reason, last_working_day, notice_date, notice_required_days, notice_treatment,
                  gratuity_decision, gratuity_note):
    if reason not in Reason.values:
        raise PayrollError("Choose why the employment is ending.")
    if last_working_day < employee.joining_date:
        raise PayrollError(f"The last working day cannot be before the joining date, {employee.joining_date:%d %b %Y}.")
    if last_working_day > timezone.localdate() + timedelta(days=366):
        raise PayrollError("The last working day is more than a year away.")
    if notice_date and notice_date > last_working_day:
        raise PayrollError("Notice cannot be given after the last working day.")
    if not 0 <= int(notice_required_days) <= 365:
        raise PayrollError("The notice required must be between 0 and 365 days.")
    if notice_treatment not in Treatment.values:
        raise PayrollError("Choose what to do about any notice shortfall.")
    if gratuity_decision is False and not (gratuity_note or "").strip():
        raise PayrollError("Say why no gratuity is payable, so the decision can be checked later.")
    month_start = date(last_working_day.year, last_working_day.month, 1)
    frozen = (PayrollLine.objects.filter(employee=employee, run__status__in=[PayrollRun.Status.APPROVED,
                                                                              PayrollRun.Status.PAID],
                                         run__period_end__gte=month_start).select_related("run").first())
    if frozen:
        raise PayrollError(f"The {frozen.run.label} payroll has already paid this person, so a settlement ending on "
                           f"{last_working_day:%d %b %Y} would pay those days twice.")


@transaction.atomic
def create_settlement(employee, *, reason, last_working_day, user, notice_date=None, notice_required_days=rules.DEFAULT_NOTICE_DAYS,
                      notice_treatment=Treatment.AUTO, leave_type=None, gratuity_decision=None, gratuity_note=""):
    employee = Employee.objects.select_for_update().select_related("company").get(pk=employee.pk)
    settlement_labor.check_eligible(employee)
    _check_inputs(employee, reason=reason, last_working_day=last_working_day, notice_date=notice_date,
                  notice_required_days=notice_required_days, notice_treatment=notice_treatment,
                  gratuity_decision=gratuity_decision, gratuity_note=gratuity_note)
    if FinalSettlement.objects.filter(employee=employee).exclude(status=Status.CANCELLED).exists():
        raise PayrollError(f"{employee.full_name} already has a final settlement.")
    case = FinalSettlement.objects.create(
        employee=employee, reason=reason, last_working_day=last_working_day, notice_date=notice_date,
        notice_required_days=notice_required_days, notice_treatment=notice_treatment,
        leave_type=leave_type or default_leave_type(employee.company), gratuity_decision=gratuity_decision,
        gratuity_note=(gratuity_note or "").strip(), prepared_by=user)
    return calculate_settlement(case)


@transaction.atomic
def update_settlement(case, **fields):
    """Change the case's inputs (only while it is a draft) and calculate again."""
    case = _locked(case)
    _need_draft(case, "changed")
    wanted = {name: fields.get(name, getattr(case, name)) for name in (
        "reason", "last_working_day", "notice_date", "notice_required_days", "notice_treatment", "gratuity_decision",
        "gratuity_note")}
    _check_inputs(case.employee, **wanted)
    for name, value in wanted.items():
        setattr(case, name, value)
    if "leave_type" in fields:
        case.leave_type = fields["leave_type"]
    case.gratuity_note = (case.gratuity_note or "").strip()
    case.save()
    return calculate_settlement(case)


# ------------------------------------------------------------------ the arithmetic

def leave_days(employee, leave_type, last_day):
    """Annual leave owed in days: every year's balance, except that the entitlement granted for the year of the last
    working day is replaced by the part actually earned by then. Leave booked after the last day is not counted as taken."""
    from apps.leave.models import LeaveLedger, LeaveRequest

    rows = list(LeaveLedger.objects.filter(employee=employee, leave_type=leave_type))
    total = sum((r.days for r in rows), ZERO)
    granted = sum((r.days for r in rows if r.kind == LeaveLedger.Kind.ENTITLEMENT and r.year == last_day.year), ZERO)
    accrued = rules.accrued_leave_days(leave_type.annual_entitlement, employee.joining_date, last_day)
    booked_later = sum((r.days for r in LeaveRequest.objects.filter(
        employee=employee, leave_type=leave_type, status=LeaveRequest.Status.APPROVED, start_date__gt=last_day)), ZERO)
    return {"balance": total, "granted_this_year": granted, "earned_this_year": accrued,
            "booked_after": booked_later, "payable": total - granted + accrued + booked_later}


def _wage_basis(record):
    return {"basic": record.basic, "gratuity_wage": record.gratuity_wage, "monthly_total": record.total}


def _calculate(case):
    """Returns (system items as (kind, label, amount), issues, details) for the case as it stands."""
    emp, last = case.employee, case.last_working_day
    month_start, month_days = date(last.year, last.month, 1), calendar.monthrange(last.year, last.month)[1]
    policy = policy_on(emp.company, last)
    if policy is None:
        raise PayrollError(f"{emp.company.name} has no payroll policy for {last:%B %Y}. Set it first (Settings, Payroll).")
    labor = emp.worker_type == Employee.WorkerType.LABOR
    if labor:
        items, issues, details = settlement_labor.wages_part(case, emp, policy, month_start, last, month_days, Kind)
    else:
        records = list(SalaryRecord.objects.filter(employee=emp, effective_from__lte=last)
                       .filter(Q(effective_to__isnull=True) | Q(effective_to__gte=month_start))
                       .prefetch_related("allowances__allowance_type").order_by("-effective_from"))
        attendance = dict(Attendance.objects.filter(employee=emp, date__range=(month_start, last)).values_list("date", "status"))
        pay = PaymentDetail.objects.filter(employee=emp).first()
        fields = calculation.compute_line(
            joining_date=emp.joining_date, period_start=month_start, period_end=last, days_in_month=policy.days_in_month,
            records=records, attendance=attendance, day_kinds=day_types(emp, month_start, last),
            today=timezone.localdate(), nationality_text=emp.nationality, policy=policy.values(),
            payment=(pay.method, pay.bank_name, pay.iban) if pay else None, month_days=month_days)
        issues, items, details = list(fields["issues"]), [], {"month_days": month_days, "policy_days": policy.days_in_month}
        first_day = max(month_start, emp.joining_date)
        items.append((Kind.WAGES, f"Salary {first_day:%d %b} to {last:%d %b %Y}", fields["gross"]))
        if fields["absence_deduction"]:
            items.append((Kind.ABSENCE, f"Absence and unpaid days ({fields['unpaid_days']:.2f} days)", -fields["absence_deduction"]))
        if fields["sio_employee"]:
            items.append((Kind.SIO, "Social insurance (SIO), employee share", -fields["sio_employee"]))
        details["wages"] = {"segments": fields["breakdown"]["segments"], "lost_days": fields["breakdown"]["lost_days"]}

    record = settlement_labor.basis(emp, last, policy.days_in_month) if labor else salary_on(emp, last)
    if record is None:
        return items, issues, details
    details["basis"] = {k: str(v) for k, v in _wage_basis(record).items()}
    if getattr(record, "note", ""):
        details["basis_note"] = record.note
    daily = money(record.total / policy.days_in_month)
    details["daily_rate"] = str(daily)

    # --- annual leave
    from apps.leave.models import LeaveType
    if case.leave_type is not None:
        owed = leave_days(emp, case.leave_type, last)
        details["leave"] = {k: str(v) for k, v in owed.items()} | {"type": case.leave_type.name}
        if owed["payable"] > 0:
            items.append((Kind.LEAVE, f"Annual leave: {owed['payable']:.1f} days at {daily}", money(owed["payable"] * daily)))
        elif owed["payable"] < 0:
            issues.append(issue("leave_advance", "warn", f"{-owed['payable']:.1f} days of leave were taken in advance. "
                                                         "Nothing is deducted automatically; add a deduction if the "
                                                         "contract allows it."))
        if owed["booked_after"]:
            issues.append(issue("leave_after", "warn", f"{owed['booked_after']:.1f} days of approved leave are booked after "
                                                       "the last working day. Cancel them."))
    elif LeaveType.objects.filter(company=emp.company, is_active=True, tracks_balance=True).exists():
        issues.append(issue("leave_type", "block", "Choose the annual leave type, so the unused leave can be paid."))
    else:
        issues.append(issue("leave_none", "warn", "The company has no leave type that tracks a balance, so no leave is paid."))

    # --- notice
    if case.reason in NEEDS_NOTICE:
        if case.notice_date is None:
            issues.append(issue("notice_date", "block", "Enter the date notice was given."))
        else:
            served, short = rules.notice_shortfall(case.notice_required_days, case.notice_date, last)
            treatment = case.notice_treatment
            if treatment == Treatment.AUTO:
                treatment = Treatment.PAY if case.reason == Reason.TERMINATION else Treatment.NONE
            details["notice"] = {"required": case.notice_required_days, "served": served, "short": short,
                                 "treatment": str(treatment)}
            if short and treatment == Treatment.PAY:
                items.append((Kind.NOTICE, f"Pay in lieu of notice ({short} days short)", money(short * daily)))
            elif short and treatment == Treatment.RECOVER:
                items.append((Kind.NOTICE, f"Notice not served ({short} days short)", -money(short * daily)))

    # --- gratuity
    kind, gcc = nationality.classify(emp.nationality), nationality.is_gcc(emp.nationality)
    if kind == nationality.BAHRAINI:
        details["gratuity"] = {"applies": False, "why": "Bahraini: covered by SIO pension, no employer gratuity."}
    elif kind == nationality.OTHER:
        decision = case.gratuity_decision
        if decision is None and (case.reason == Reason.SUMMARY or gcc):
            issues.append(issue("gratuity_decision", "block", "Decide whether gratuity is payable (a dismissal without notice, "
                                                              "or a GCC national, is not decided by the standard rule)."))
        else:
            decision = True if decision is None else decision
            g = rules.gratuity(emp.joining_date, last, record.gratuity_wage)
            details["gratuity"] = {"applies": decision, "wage": str(record.gratuity_wage),
                                   **{k: str(v.quantize(Decimal("0.0001")) if hasattr(v, "quantize") else v) for k, v in g.items()}}
            if decision and g["employer"] > 0:
                items.append((Kind.GRATUITY, f"Gratuity for service before 1 March 2024 ({g['years_before_scheme']:.2f} years)",
                              g["employer"]))
            if decision and g["sio_estimate"] > 0:
                issues.append(issue("sio_gratuity", "info", f"The SIO pays the employee about {g['sio_estimate']} BHD for service "
                                                             "since 1 March 2024. Check the SIO contributions are paid up."))
    return items, issues, details


@transaction.atomic
def calculate_settlement(case):
    """Rebuild the system lines from the pay records, attendance, leave balance and rules as they are now. Lines HR added
    and charges picked up are kept."""
    case = _locked(case)
    _need_draft(case, "recalculated")
    items, issues, details = _calculate(case)
    case.items.filter(is_system=True).delete()
    for n, (kind, label, amount) in enumerate(items):
        SettlementItem.objects.create(settlement=case, kind=kind, label=label, amount=money(amount), is_system=True, position=n)
    from .loan_services import settlement_loan_lines

    issues.extend(settlement_loan_lines(case))
    total = sum((i.amount for i in case.items.all()), ZERO)
    if total < 0:
        issues.append(issue("negative_total", "block", "The person owes more than they are owed. Reduce a deduction, or "
                                                       "agree the balance with them first."))
    pay = PaymentDetail.objects.filter(employee=case.employee).first()
    case.total, case.issues, case.details = money(total), issues, details
    case.payment_method, case.bank_name, case.iban = (pay.method, pay.bank_name, pay.iban) if pay else ("", "", "")
    case.calculated_at, case.note = timezone.now(), ""
    case.save()
    return case


# ------------------------------------------------------------------ lines HR adds, and charges

@transaction.atomic
def add_item(case, *, kind, label, amount, source="manual", source_id=None):
    case = _locked(case)
    _need_draft(case, "changed")
    if kind not in (Kind.ADDITION, Kind.DEDUCTION):
        raise PayrollError("Choose whether it is an addition or a deduction.")
    label = " ".join((label or "").split())
    if not label:
        raise PayrollError("Say what it is for.")
    try:
        amount = Decimal(amount)
    except Exception:
        raise PayrollError("The amount must be a number.")
    if amount <= 0 or amount != money(amount):
        raise PayrollError("The amount must be more than zero, with at most three decimal places.")
    item = SettlementItem.objects.create(settlement=case, kind=kind, label=label[:160], source=source, source_id=source_id,
                                         amount=amount if kind == Kind.ADDITION else -amount,
                                         position=100 + case.items.count())
    calculate_settlement(case)
    return item


@transaction.atomic
def remove_item(item):
    case = _locked(item.settlement)
    _need_draft(case, "changed")
    if item.is_system:
        raise PayrollError("That line is worked out by the system. Change the case instead.")
    SettlementItem.objects.filter(pk=item.pk).delete()
    calculate_settlement(case)


def charge_candidates(case, user):
    """Renewal fees and vehicle fines already marked 'charge to the employee' that nobody has deducted yet, for this
    person: [(source, id, category label, amount)]."""
    from apps.compliance.models import RenewalPayment
    from apps.vehicles.incidents import Fine

    from .payrun import PayrollAdjustment

    taken = set(PayrollAdjustment.objects.filter(source_id__isnull=False).exclude(run__status=PayrollRun.Status.CANCELLED)
                .values_list("source", "source_id"))
    taken |= set(SettlementItem.objects.filter(source_id__isnull=False).exclude(settlement__status=Status.CANCELLED)
                 .values_list("source", "source_id"))
    found = []
    for p in (RenewalPayment.objects.for_user(user).filter(charged_to_employee=True, task__document__employee_id=case.employee_id)
              .select_related("task__document")):
        if ("renewal", p.pk) not in taken and p.total > 0:
            found.append(("renewal", p.pk, f"{p.task.document.label}: {p.description or 'renewal'}", p.total))
    for f in (Fine.objects.for_user(user).filter(is_voided=False, charged_to_employee=True, recovered_on__isnull=True,
                                                 driver_id=case.employee_id).select_related("vehicle")):
        if ("fine", f.pk) not in taken and f.display_amount > 0:
            found.append(("fine", f.pk, f"Fine {f.reference or f.pk}, {f.offence} ({f.vehicle.plate_number})", f.display_amount))
    return found


@transaction.atomic
def import_charges(case, user):
    case = _locked(case)
    _need_draft(case, "changed")
    added = 0
    for source, source_id, label, amount in charge_candidates(case, user):
        add_item(case, kind=Kind.DEDUCTION, label=label, amount=amount, source=source, source_id=source_id)
        added += 1
    return added


# ------------------------------------------------------------------ approval and payment

@transaction.atomic
def submit_settlement(case, user):
    case = _locked(case)
    _need_draft(case, "sent for approval")
    if case.blocking:
        raise PayrollError(f"{len(case.blocking)} problem{'s' if len(case.blocking) != 1 else ''} to fix first. "
                           "Read the red messages on the case.")
    employee = getattr(user, "employee", None)
    if employee is None:
        raise PayrollError("Your login is not linked to an employee record, so the approval cannot be sent in your name. "
                           "Ask the system administrator to link it.")
    try:
        approvals.submit(FLOW_CODE, case, employee=employee, requested_by=user)
    except approvals.ApprovalError as exc:
        raise PayrollError(str(exc)) from exc
    case.status, case.note = Status.SUBMITTED, ""
    case.save()
    return case


@transaction.atomic
def withdraw_settlement(case, user):
    case = _locked(case)
    if case.status != Status.SUBMITTED:
        raise PayrollError("This settlement is not waiting for approval.")
    ct = ContentType.objects.get_for_model(FinalSettlement)
    approval = ApprovalRequest.objects.filter(content_type=ct, object_id=str(case.pk), status=ApprovalRequest.Status.PENDING).first()
    if approval is None:
        raise PayrollError("This settlement is not waiting for approval.")
    try:
        approvals.cancel(approval.pk, user)
    except approvals.ApprovalError as exc:
        raise PayrollError(str(exc)) from exc
    return FinalSettlement.objects.get(pk=case.pk)


@transaction.atomic
def finalize(approval):
    """Called when the approval finishes, whatever the outcome. Idempotent. On approval the person is marked Separated and
    the fines deducted count as recovered."""
    case = FinalSettlement.objects.select_for_update().select_related("employee").get(pk=approval.object_id)
    if case.status != Status.SUBMITTED:
        return case
    if approval.status == ApprovalRequest.Status.APPROVED:
        case.status, case.approved_at = Status.APPROVED, timezone.now()
        case.note = _recover_fines(case)
        person = Employee.objects.select_for_update().get(pk=case.employee_id)
        if person.status != Employee.Status.SEPARATED:
            person.status = Employee.Status.SEPARATED
            person.save(update_fields=["status"])
    else:
        last = approval.actions.filter(decision=ApprovalAction.Decision.REJECT).order_by("-acted_at").first()
        case.status = Status.DRAFT
        case.note = (f"Rejected: {last.comment}" if last and last.comment else
                     "Rejected." if approval.status == ApprovalRequest.Status.REJECTED else "Taken back for changes.")
    case.save()
    return case


def _recover_fines(case):
    from apps.vehicles import recoveries
    from apps.vehicles.incidents import Fine
    from apps.vehicles.services import VehicleError

    problems = []
    for item in case.items.filter(source="fine", source_id__isnull=False):
        fine = Fine.objects.filter(pk=item.source_id).first()
        if fine is None:
            continue
        on = max(fine.fined_on, min(case.last_working_day, timezone.localdate()))
        try:
            recoveries.mark_recovered(fine, on, "Deducted in the final settlement")
        except VehicleError as exc:
            problems.append(f"Fine {fine.reference or fine.pk}: {exc}")
    from .loan_services import recover_settlement_loans

    problems += recover_settlement_loans(case)
    return ("Approved, but check: " + "; ".join(problems))[:500] if problems else ""


@transaction.atomic
def cancel_settlement(case):
    case = _locked(case)
    _need_draft(case, "cancelled")
    case.status = Status.CANCELLED
    case.save()
    return case


@transaction.atomic
def mark_paid(case, paid_on):
    case = _locked(case)
    if case.status != Status.APPROVED:
        raise PayrollError("A payment is recorded only for an approved settlement.")
    if paid_on > timezone.localdate():
        raise PayrollError("The payment date cannot be in the future.")
    case.status, case.paid_on = Status.PAID, paid_on
    case.save()
    return case
