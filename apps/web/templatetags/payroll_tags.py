from django import template
from django.utils import timezone

from apps.employees.models import Employee
from apps.payroll import services

register = template.Library()


@register.inclusion_tag("web/payroll/_staff_card.html", takes_context=True)
def employee_pay(context, employee):
    """The pay card for a staff member's page: {% employee_pay employee %}.
    Shows nothing to someone without the right to see salaries, nor for labor supplied by a contractor."""
    user = context["request"].user
    if not user.has_perm("payroll.view_salaryrecord"):
        return {"allowed": False}
    profile = getattr(employee, "labor_profile", None)
    if profile is not None and profile.engagement == "contracted":
        return {"allowed": False}
    is_staff = employee.worker_type == Employee.WorkerType.STAFF
    record = services.salary_on(employee, timezone.localdate()) if is_staff else None
    return {"allowed": True, "employee": employee, "is_staff": is_staff, "record": record,
            "payment": getattr(employee, "payment_detail", None)}
