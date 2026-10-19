"""Family status: ask, HR sends it to the manager, the manager decides.

An approval gives the person family status (an "allowed" decision on them, see eligibility.py). That is what lets HR
record their family and ask for a new family visa. Only the manager's step goes through the approval engine; the
HR review of an employee's own request is part of the request itself, because nobody may approve a request they
raised."""
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.utils import timezone

from apps.core import approvals
from apps.core.models import ApprovalRequest
from apps.employees.models import Employee

from . import eligibility
from .status import FamilyStatusRequest

Status = FamilyStatusRequest.Status
FLOW_CODE = "dependants.status"


class StatusError(Exception):
    pass


def _clean(text):
    return " ".join((text or "").split())


def open_request(employee):
    return FamilyStatusRequest.objects.filter(employee=employee, status__in=FamilyStatusRequest.OPEN).first()


def _lock(req):
    return (FamilyStatusRequest.objects.select_for_update().select_related("employee", "requested_by")
            .get(pk=req.pk))


def _send(req, user):
    """Hand the request to the reporting manager. Raises StatusError (and so rolls back) if there is no one to ask."""
    try:
        approvals.submit(FLOW_CODE, req, employee=req.employee, requested_by=user)
    except approvals.ApprovalError as exc:
        raise StatusError(str(exc)) from exc
    req.status = Status.PENDING
    req.save(update_fields=["status", "updated_at"])


@transaction.atomic
def ask(employee, *, requested_by, reason=""):
    """The employee asks (it waits for HR), or HR asks for them (it goes straight to the manager)."""
    emp = Employee.objects.select_for_update().select_related("designation").get(pk=employee.pk)
    if emp.status == Employee.Status.SEPARATED:
        raise StatusError(f"{emp.full_name} has left the company.")
    if eligibility.sponsor_status(emp).allowed:
        raise StatusError(f"{emp.full_name} already has family status.")
    if open_request(emp) is not None:
        raise StatusError(f"There is already a family status request in progress for {emp.full_name}.")
    from_employee = bool(emp.user_id) and requested_by.pk == emp.user_id
    req = FamilyStatusRequest.objects.create(employee=emp, requested_by=requested_by,
                                             from_employee=from_employee, reason=_clean(reason))
    if not from_employee:
        _send(req, requested_by)
    return req


@transaction.atomic
def forward(req, user):
    """HR sends an employee's request to the reporting manager."""
    req = _lock(req)
    if req.status != Status.WAITING_HR:
        raise StatusError("This request is not waiting for HR.")
    _send(req, user)
    return req


@transaction.atomic
def decline(req, note):
    """HR turns an employee's request down. The reason is shown to the employee."""
    req = _lock(req)
    if req.status != Status.WAITING_HR:
        raise StatusError("This request is not waiting for HR.")
    note = _clean(note)
    if not note:
        raise StatusError("Give the reason for declining.")
    req.status, req.hr_note = Status.DECLINED, note
    req.save(update_fields=["status", "hr_note", "updated_at"])
    return req


def _pending_approval(req):
    ct = ContentType.objects.get_for_model(FamilyStatusRequest)
    return ApprovalRequest.objects.filter(content_type=ct, object_id=str(req.pk),
                                          status=ApprovalRequest.Status.PENDING).first()


@transaction.atomic
def withdraw(req, user, *, hr=False):
    """Take a request back. The person who asked may do it while it waits for HR; once it is with the manager,
    the HR user who sent it may withdraw it."""
    req = _lock(req)
    if req.status == Status.WAITING_HR:
        if not hr and user.pk != req.requested_by_id:
            raise StatusError("Only the person who asked can withdraw it.")
        req.status = Status.WITHDRAWN
        req.save(update_fields=["status", "updated_at"])
        return req
    if req.status == Status.PENDING:
        approval = _pending_approval(req)
        if approval is None:
            raise StatusError("This request is not waiting for approval.")
        try:
            approvals.cancel(approval.pk, user)               # finishes the approval, which withdraws the request
        except approvals.ApprovalError as exc:
            raise StatusError(str(exc)) from exc
        return FamilyStatusRequest.objects.get(pk=req.pk)
    raise StatusError("This request has already been decided.")


@transaction.atomic
def finalize(approval):
    """Called when the manager's approval finishes, whatever the outcome. Idempotent."""
    req = (FamilyStatusRequest.objects.select_for_update().select_related("employee")
           .get(pk=approval.object_id))
    if req.status != Status.PENDING:
        return req
    if approval.status == ApprovalRequest.Status.APPROVED:
        last = (approval.actions.filter(decision="approve").select_related("actor").order_by("-acted_at").first())
        who = (last.actor.get_full_name() or last.actor.get_username()) if last else "the manager"
        req.status, req.decided_note = Status.APPROVED, (last.comment if last else "")
        req.save(update_fields=["status", "decided_note", "updated_at"])
        eligibility.set_exception(req.employee, "allowed",
                                  f"Approved by {who} on {timezone.localdate():%d %b %Y}")
    elif approval.status == ApprovalRequest.Status.REJECTED:
        last = approval.actions.filter(decision="reject").order_by("-acted_at").first()
        req.status, req.decided_note = Status.REJECTED, (last.comment if last else "")
        req.save(update_fields=["status", "decided_note", "updated_at"])
    else:
        req.status = Status.WITHDRAWN
        req.save(update_fields=["status", "updated_at"])
    return req


def progress(req):
    """The steps of one request, for the little progress line: [{'label', 'state'}], where state is done, current,
    stopped or later."""
    S, state = Status, req.status
    rows = [{"label": "Asked", "state": "done"}]
    if req.from_employee:
        rows.append({"label": "HR", "state": {S.WAITING_HR: "current", S.DECLINED: "stopped",
                                              S.WITHDRAWN: "later"}.get(state, "done")})
    rows.append({"label": "Manager", "state": {S.PENDING: "current", S.REJECTED: "stopped",
                                               S.APPROVED: "done"}.get(state, "later")})
    rows.append({"label": "Family status", "state": {S.APPROVED: "done", S.DECLINED: "stopped",
                                                     S.REJECTED: "stopped", S.WITHDRAWN: "stopped"}.get(state, "later")})
    return rows
