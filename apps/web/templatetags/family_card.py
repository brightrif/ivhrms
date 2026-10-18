"""The Family card at the top of an employee's page, the count of requests waiting for HR, and the note on a family
member's visa card when the sponsor has no family status."""
from django import template

from apps.dependants import eligibility, status_services, visa_services
from apps.dependants.models import Dependant
from apps.dependants.status import FamilyStatusRequest

register = template.Library()
Status = FamilyStatusRequest.Status


@register.inclusion_tag("web/family/_employee_card.html", takes_context=True)
def employee_family_card(context, emp):
    request = context.get("request")
    user = context.get("user") or getattr(request, "user", None)
    if user is None or not user.has_perm("dependants.view_dependant"):
        return {"show": False}
    left = emp.status == "separated"
    status = eligibility.sponsor_status(emp)
    open_req = status_services.open_request(emp)
    last = (FamilyStatusRequest.objects.filter(employee=emp).exclude(status__in=FamilyStatusRequest.OPEN).first()
            if not status.allowed and open_req is None else None)
    if last is not None and last.status not in (Status.DECLINED, Status.REJECTED):
        last = None
    can_change_request = user.has_perm("dependants.change_familystatusrequest")
    members = list(Dependant.objects.for_user(user).filter(employee=emp, is_active=True)
                   .order_by("relationship", "date_of_birth", "id"))
    return {
        "show": True, "emp": emp, "status": status, "members": members, "last": last,
        "open_req": open_req, "progress": status_services.progress(open_req) if open_req else [],
        "manager": emp.reporting_manager,
        "can_request": (user.has_perm("dependants.add_familystatusrequest") and not left
                        and not status.allowed and open_req is None),
        "can_forward": bool(can_change_request and open_req and open_req.status == Status.WAITING_HR),
        "can_add": user.has_perm("dependants.add_dependant") and not left and status.allowed,
        "can_remove": (user.has_perm("dependants.change_familyvisaexception") and not left
                       and (status.allowed or status.source == "exception")),
        "csrf_token": context.get("csrf_token"),
        "next_url": request.get_full_path() if request is not None else ""}


@register.simple_tag(takes_context=True)
def status_waiting(context):
    """How many family status requests are waiting for HR, for the tab badge."""
    request = context.get("request")
    user = context.get("user") or getattr(request, "user", None)
    if user is None or not user.has_perm("dependants.view_familystatusrequest"):
        return 0
    return FamilyStatusRequest.objects.for_user(user).filter(status=Status.WAITING_HR).count()


@register.simple_tag
def visa_block_note(dep):
    """Why there is no 'New request' button, when the reason is the sponsor. Empty otherwise."""
    if not dep.is_active or visa_services.current_visa(dep) is not None:
        return ""
    status = eligibility.sponsor_status(dep.employee)
    return "" if status.allowed else f"{status.text}. A new visa needs family status first."
