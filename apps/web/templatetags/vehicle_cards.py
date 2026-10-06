from django import template

from apps.vehicles import fuel, maintenance

register = template.Library()


@register.inclusion_tag("web/vehicles/_fuel_card.html", takes_context=True)
def fuel_card(context, vehicle):
    stats = fuel.fuel_stats(vehicle)
    recent = [f for f in stats["rows"] if not f.is_voided][:3]
    return {"vehicle": vehicle, "stats": stats, "recent": recent, "perms": context.get("perms")}


@register.inclusion_tag("web/vehicles/_service_card.html", takes_context=True)
def service_card(context, vehicle):
    return {"vehicle": vehicle, "plans": maintenance.vehicle_plans(vehicle)[:5], "perms": context.get("perms")}
