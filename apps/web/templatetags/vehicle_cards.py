from django import template
from django.utils import timezone

from apps.vehicles import attachments, financing, fines, fuel, handovers, maintenance, reports

register = template.Library()


@register.inclusion_tag("web/vehicles/_fuel_card.html", takes_context=True)
def fuel_card(context, vehicle):
    stats = fuel.fuel_stats(vehicle)
    recent = [f for f in stats["rows"] if not f.is_voided][:3]
    return {"vehicle": vehicle, "stats": stats, "recent": recent, "perms": context.get("perms")}


@register.inclusion_tag("web/vehicles/_service_card.html", takes_context=True)
def service_card(context, vehicle):
    return {"vehicle": vehicle, "plans": maintenance.vehicle_plans(vehicle)[:5], "perms": context.get("perms")}


@register.inclusion_tag("web/vehicles/_incident_card.html", takes_context=True)
def incident_card(context, vehicle):
    open_accidents = vehicle.accidents.filter(is_voided=False, status="open").count()
    return {"vehicle": vehicle, "unpaid": fines.unpaid_summary(vehicle), "open_accidents": open_accidents,
            "perms": context.get("perms")}


@register.inclusion_tag("web/vehicles/_loan_card.html", takes_context=True)
def loan_card(context, vehicle):
    """Loan figures are for Finance and Management: the detail page only calls this for people who may see them."""
    loan = financing.current_loan(vehicle)
    return {"vehicle": vehicle, "loan": loan, "summary": financing.loan_summary(loan) if loan else None,
            "perms": context.get("perms")}


@register.inclusion_tag("web/vehicles/_cost_card.html", takes_context=True)
def cost_card(context, vehicle):
    """This year's running cost of one vehicle. Money, so the detail page only calls it for Finance and Management."""
    user = context.get("user") or getattr(context.get("request"), "user", None)
    year = timezone.localdate().year
    report = reports.cost_report(user, year, vehicle_id=vehicle.pk) if user else {"rows": []}
    return {"vehicle": vehicle, "year": year, "row": report["rows"][0] if report["rows"] else None,
            "perms": context.get("perms")}


@register.inclusion_tag("web/vehicles/_custody_panel.html", takes_context=True)
def custody_panel(context, assignment, vehicle):
    """What is being done about a vehicle whose holder is on notice or has left. Empty for an active holder."""
    employee = assignment.employee
    state = {"on_notice": "notice", "separated": "left"}.get(employee.status)
    return {"assignment": assignment, "vehicle": vehicle, "employee": employee, "state": state,
            "decision": handovers.current_decision(assignment) if state else None, "perms": context.get("perms")}


def _file_context(context, obj, target_kind):
    user = context.get("user") or getattr(context.get("request"), "user", None)
    can_view = bool(user) and attachments.can_view(user, target_kind)
    can_add = bool(user) and attachments.can_change(user, target_kind) and not getattr(obj, "is_voided", False)
    return {"obj": obj, "target_kind": target_kind, "can_view": can_view, "can_add": can_add,
            "files": list(attachments.files_of(obj)) if can_view else []}


@register.inclusion_tag("web/vehicles/_file_links.html", takes_context=True)
def file_links(context, obj, target_kind):
    """The files on one row of a table, as small links, with an Attach link for those who may add."""
    return _file_context(context, obj, target_kind)


@register.inclusion_tag("web/vehicles/_file_panel.html", takes_context=True)
def file_panel(context, obj, target_kind):
    """The files on one entry, as a card (for an accident or a loan)."""
    return _file_context(context, obj, target_kind)
