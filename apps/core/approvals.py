from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.utils import timezone

from apps.audit import services as audit
from .models import ApprovalAction, ApprovalFlow, ApprovalRequest, ApprovalStep
from .signals import approval_completed

Status = ApprovalRequest.Status
Decision = ApprovalAction.Decision


class ApprovalError(Exception):
    pass

class ApprovalDenied(ApprovalError):
    """The user may not act on this request right now."""
    

def _current_step(req):
    return req.flow.steps.filter(order=req.current_step).first()


def approver_ids(step, req):
    User = get_user_model()
    if step.approver_type == ApprovalStep.ApproverType.REPORTING_MANAGER:
        mgr = req.employee.reporting_manager
        return {mgr.user_id} if mgr and mgr.user_id else set()
    if step.approver_type == ApprovalStep.ApproverType.GROUP:
        return set(User.objects.filter(
            groups=step.group_id, is_active=True,
            company_access__company_id=req.employee.company_id,
        ).values_list("id", flat=True))
    return {step.user_id} if step.user_id else set()


def can_act(req, user):
    if req.status != Status.PENDING or req.current_step is None:
        return False
    # nobody approves their own request
    if user.id == req.requested_by_id or user.id == req.employee.user_id:
        return False
    step = _current_step(req)
    return step is not None and user.id in approver_ids(step, req)


@transaction.atomic
def submit(flow_code, target, *, employee, requested_by):
    flows = ApprovalFlow.objects.filter(code=flow_code, is_active=True)
    flow = (flows.filter(company_id=employee.company_id).first()
            or flows.filter(company__isnull=True).first())
    if flow is None:
        raise ApprovalError(f"No approval flow '{flow_code}' is configured for this company.")

    first = flow.steps.first()
    if first is None:
        raise ApprovalError(f"Approval flow '{flow_code}' has no steps.")

    ct = ContentType.objects.get_for_model(target)
    if ApprovalRequest.objects.filter(content_type=ct, object_id=str(target.pk),
                                      status=Status.PENDING).exists():
        raise ApprovalError("This item already has a pending approval.")

    req = ApprovalRequest.objects.create(
        flow=flow, company=employee.company, content_type=ct, object_id=str(target.pk),
        employee=employee, requested_by=requested_by, current_step=first.order,
    )
    if not approver_ids(first, req):
        hint = (" Ask HR to assign a reporting manager."
                if first.approver_type == ApprovalStep.ApproverType.REPORTING_MANAGER else "")
        raise ApprovalError(f"No approver is available for the '{first.name}' step.{hint}")
    return req


def _complete(req, status):
    req.status = status
    req.current_step = None
    req.completed_at = timezone.now()
    req.save()
    # Sent synchronously, inside the caller's transaction. If a receiver (leave, attendance...)
    # raises, the whole decision rolls back. Receivers that need after-commit work
    # (e.g. notifications) should use transaction.on_commit themselves.
    approval_completed.send(sender=ApprovalRequest, approval=req)


def decide(request_id, user, decision, *, comment="", channel="web"):
    """Public entry point. A denied attempt is audited outside the transaction so the row survives."""
    try:
        return _decide(request_id, user, decision, comment=comment, channel=channel)
    except ApprovalDenied:
        req = ApprovalRequest.objects.get(pk=request_id)
        audit.log("permission_denied", req, module="approvals", actor=user, channel=channel,
                  subject_employee_id=req.employee_id, company_id=req.company_id)
        raise


@transaction.atomic
def _decide(request_id, user, decision, *, comment="", channel="web"):
    # select_for_update locks the row on PostgreSQL (it is a no-op on SQLite)
    req = (ApprovalRequest.objects.select_for_update()
           .select_related("flow", "employee__reporting_manager").get(pk=request_id))
    if not can_act(req, user):
        raise ApprovalDenied("You cannot act on this request.")
    if decision == Decision.REJECT and not comment.strip():
        raise ApprovalError("A comment is required when rejecting.")

    step = _current_step(req)
    ApprovalAction.objects.create(request=req, step=step, actor=user, decision=decision,
                                  comment=comment, channel=channel)
    audit.log(decision, req, module="approvals", actor=user, channel=channel, reason=comment,
              subject_employee_id=req.employee_id, company_id=req.company_id)

    if decision == Decision.REJECT:
        _complete(req, Status.REJECTED)
    else:
        nxt = req.flow.steps.filter(order__gt=step.order).first()
        if nxt:
            req.current_step = nxt.order
            req.save(update_fields=["current_step"])
        else:
            _complete(req, Status.APPROVED)
    return req


@transaction.atomic
def cancel(request_id, user):
    req = ApprovalRequest.objects.select_for_update().get(pk=request_id)
    if req.status != Status.PENDING or user.id != req.requested_by_id:
        raise ApprovalError("Only the requester can cancel a pending request.")
    _complete(req, Status.CANCELLED)
    return req


def pending_for(user):
    """Requests waiting on this user. Fine for now; optimise when volumes grow."""
    qs = (ApprovalRequest.objects.filter(status=Status.PENDING)
          .select_related("flow", "employee__reporting_manager"))
    return [r for r in qs if can_act(r, user)]