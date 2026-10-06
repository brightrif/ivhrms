from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.utils import timezone

from apps.core import approvals
from apps.core.models import ApprovalRequest
from apps.employees.models import Employee
from apps.scheduling.services import HOLIDAY, WEEKLY_OFF, day_types

from .models import LeaveLedger, LeaveRequest
from .signals import leave_approved, leave_cancelled

FLOW_CODE = "leave.request"
Status = LeaveRequest.Status
Kind = LeaveLedger.Kind


class LeaveError(Exception):
    pass


def _sum(values):
    return sum(values, Decimal("0"))      # sum in Python, not in SQL (see the SQLite decimal note)


def balance(employee, leave_type, year):
    return _sum(LeaveLedger.objects.filter(employee=employee, leave_type=leave_type, year=year)
                .values_list("days", flat=True))


def reserved(employee, leave_type, year):
    """Days held by pending requests, so a second request can't use the same days."""
    return _sum(LeaveRequest.objects.filter(employee=employee, leave_type=leave_type,
                                            status=Status.PENDING, start_date__year=year)
                .values_list("days", flat=True))


def available(employee, leave_type, year):
    return balance(employee, leave_type, year) - reserved(employee, leave_type, year)


def compute_days(employee, leave_type, start, end, is_half_day=False):
    if end < start:
        raise LeaveError("End date is before start date.")
    if start.year != end.year:
        raise LeaveError("A request cannot span two calendar years. Please submit one request per year.")
    if is_half_day and (start != end or not leave_type.allow_half_day):
        raise LeaveError("Half-day leave must be a single day and an allowed leave type.")
    days = Decimal("0")
    for kind in day_types(employee, start, end).values():
        if kind == HOLIDAY and leave_type.exclude_holidays:
            continue
        if kind == WEEKLY_OFF and leave_type.exclude_weekly_offs:
            continue
        days += 1
    if days == 0:
        raise LeaveError("The selected dates contain no chargeable leave days.")
    return Decimal("0.5") if is_half_day else days


@transaction.atomic
def apply(employee, leave_type, start, end, *, requested_by, is_half_day=False, reason="", attachment=None):
    Employee.objects.select_for_update().get(pk=employee.pk)    # serialises concurrent requests on PostgreSQL
    if leave_type.company_id != employee.company_id or not leave_type.is_active:
        raise LeaveError("This leave type is not available.")
    if employee.status == Employee.Status.SEPARATED:
        raise LeaveError("This employee has left the company.")
    if leave_type.requires_attachment and not attachment:
        raise LeaveError("A supporting document is required for this leave type.")

    days = compute_days(employee, leave_type, start, end, is_half_day)

    if LeaveRequest.objects.filter(employee=employee, status__in=[Status.PENDING, Status.APPROVED],
                                   start_date__lte=end, end_date__gte=start).exists():
        raise LeaveError("These dates overlap an existing leave request.")
    if leave_type.tracks_balance:
        avail = available(employee, leave_type, start.year)
        if days > avail:
            raise LeaveError(f"Insufficient balance: {avail} day(s) available, {days} requested.")

    req = LeaveRequest.objects.create(
        employee=employee, leave_type=leave_type, start_date=start, end_date=end,
        is_half_day=is_half_day, days=days, reason=reason, attachment=attachment or "")
    try:
        approvals.submit(FLOW_CODE, req, employee=employee, requested_by=requested_by)
    except approvals.ApprovalError as exc:
        raise LeaveError(str(exc)) from exc       # raising inside atomic() rolls the request back
    return req


@transaction.atomic
def finalize(approval):
    """Called when the approval finishes (any outcome). Idempotent."""
    req = (LeaveRequest.objects.select_for_update()
           .select_related("leave_type", "employee").get(pk=approval.object_id))
    if req.status != Status.PENDING:
        return req
    if approval.status == ApprovalRequest.Status.APPROVED:
        req.status = Status.APPROVED
        req.save(update_fields=["status"])
        if req.leave_type.tracks_balance:
            LeaveLedger.objects.create(employee=req.employee, leave_type=req.leave_type,
                                       year=req.start_date.year, kind=Kind.TAKEN, days=-req.days,
                                       request=req, remarks="Approved leave")
        leave_approved.send(sender=LeaveRequest, leave_request=req)
    else:
        req.status = (Status.REJECTED if approval.status == ApprovalRequest.Status.REJECTED
                      else Status.CANCELLED)
        req.save(update_fields=["status"])
    return req


@transaction.atomic
def cancel(request_id, user):
    req = (LeaveRequest.objects.select_for_update()
           .select_related("leave_type", "employee").get(pk=request_id))
    if req.status == Status.PENDING:
        ct = ContentType.objects.get_for_model(LeaveRequest)
        approval = ApprovalRequest.objects.get(content_type=ct, object_id=str(req.pk),
                                               status=ApprovalRequest.Status.PENDING)
        try:
            approvals.cancel(approval.pk, user)           # triggers finalize() -> CANCELLED
        except approvals.ApprovalError as exc:
            raise LeaveError(str(exc)) from exc
    elif req.status == Status.APPROVED:
        if not (user.id == req.employee.user_id or user.has_perm("leave.cancel_any_leave")):
            raise LeaveError("You cannot cancel this leave.")
        if req.start_date <= timezone.localdate():
            raise LeaveError("Leave that has already started must be adjusted by HR.")
        if req.leave_type.tracks_balance:
            LeaveLedger.objects.create(employee=req.employee, leave_type=req.leave_type,
                                       year=req.start_date.year, kind=Kind.REVERSAL, days=req.days,
                                       request=req, remarks="Leave cancelled")
        req.status = Status.CANCELLED
        req.save(update_fields=["status"])
        leave_cancelled.send(sender=LeaveRequest, leave_request=req)
    else:
        raise LeaveError("Only pending or approved requests can be cancelled.")
    req.refresh_from_db()
    return req


def _round_half(x):
    return (x * 2).quantize(Decimal("1"), rounding=ROUND_HALF_UP) / 2


def grant_entitlement(employee, leave_type, year):
    """Idempotent. Joiners in the grant year get a pro-rata amount (placeholder policy: confirm with the client)."""
    if not leave_type.tracks_balance or leave_type.annual_entitlement <= 0:
        return None
    if employee.joining_date.year > year:
        return None
    if LeaveLedger.objects.filter(employee=employee, leave_type=leave_type, year=year,
                                  kind=Kind.ENTITLEMENT).exists():
        return None
    days = leave_type.annual_entitlement
    if employee.joining_date.year == year:
        remaining = (date(year, 12, 31) - employee.joining_date).days + 1
        total = (date(year, 12, 31) - date(year, 1, 1)).days + 1
        days = _round_half(days * Decimal(remaining) / Decimal(total))
    return LeaveLedger.objects.create(employee=employee, leave_type=leave_type, year=year,
                                      kind=Kind.ENTITLEMENT, days=days, remarks=f"{year} entitlement")