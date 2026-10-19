"""Who approves family status requests.

By default it is the employee's reporting manager. A company can instead name ONE person who approves every family
status request of that company (a company with a single manager, an owner who decides). The approval engine already
has a step type for a specific user, so the company gets its own one-step copy of the flow; it takes over from the
built-in default. Switching back only switches that copy off, so the default applies again.

Requests already waiting stay with the approver they were sent to, with one exception: if the named person is replaced
by another, the requests waiting for the first go to the second (the engine reads the step as it is now)."""
from django.db import transaction

from apps.audit import services as audit
from apps.core.models import ApprovalFlow, ApprovalStep

from .status_defaults import FLOW_CODE

USER = ApprovalStep.ApproverType.USER
STEP_NAME = "Family status approver"


class ApproverError(Exception):
    pass


def _flow(company):
    return ApprovalFlow.objects.filter(company=company, code=FLOW_CODE).first()


def approver_for(company):
    """The person named for this company, or None when the reporting manager decides."""
    flow = _flow(company)
    if flow is None or not flow.is_active:
        return None
    step = flow.steps.filter(approver_type=USER).order_by("order").first()
    return step.user if step else None


@transaction.atomic
def set_approver(actor, company, user):
    if user is None or not user.is_active:
        raise ApproverError("Choose a person who can sign in.")
    flow = ApprovalFlow.objects.select_for_update().filter(company=company, code=FLOW_CODE).first()
    if flow is None:
        flow = ApprovalFlow.objects.create(company=company, code=FLOW_CODE, name="Family status request", is_active=True)
    steps = list(flow.steps.order_by("order"))
    ours = len(steps) == 1 and steps[0].approver_type == USER and steps[0].name == STEP_NAME
    if steps and not ours:                                      # someone set up other steps for this company
        raise ApproverError(f"{company.name} already has its own approval steps for this, set up elsewhere. "
                            "Change them there.")
    if steps:
        step = steps[0]
        step.approver_type, step.user, step.group, step.name = USER, user, None, STEP_NAME
        step.save()
    else:
        ApprovalStep.objects.create(flow=flow, order=1, name=STEP_NAME, approver_type=USER, user=user)
    if not flow.is_active:
        flow.is_active = True
        flow.save()
    audit.log("update", flow, module="dependants", company_id=company.pk, subject_employee_id=None,
              reason=f"{actor.get_username()} made {user.get_full_name() or user.get_username()} the approver of family "
                     f"status requests for {company.name}")
    return flow


@transaction.atomic
def clear_approver(actor, company):
    """Back to the reporting manager. Returns True if there was a named approver."""
    flow = _flow(company)
    if flow is None or not flow.is_active:
        return False
    flow.is_active = False
    flow.save()
    audit.log("update", flow, module="dependants", company_id=company.pk, subject_employee_id=None,
              reason=f"{actor.get_username()} went back to the reporting manager approving family status for {company.name}")
    return True


def approver_context(user):
    """For the Family Visas tab of Settings: one line per company."""
    from apps.organization.services import companies_for

    return {"approver_rows": [{"company": c, "approver": approver_for(c)} for c in companies_for(user)]}
