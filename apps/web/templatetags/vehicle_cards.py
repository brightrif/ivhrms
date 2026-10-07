from django import template

from apps.vehicles import financing, fines, fuel, maintenance

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
