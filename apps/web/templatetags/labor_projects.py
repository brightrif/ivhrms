from django import template

from apps.labor import deployment

register = template.Library()


@register.filter
def open_projects(employee):
    """Every project the worker is on now, the main one first."""
    return deployment.open_allocations(employee)
