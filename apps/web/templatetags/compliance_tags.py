from django import template

from apps.compliance.models import Document
from apps.employees.models import Employee

register = template.Library()


@register.inclusion_tag("web/compliance/_employee_documents.html", takes_context=True)
def employee_documents(context, employee):
    """The documents card for a staff member's page: {% employee_documents employee %}.
    Shows nothing to someone who may not view compliance documents."""
    user = context["request"].user
    if not user.has_perm("compliance.view_document"):
        return {"allowed": False}
    documents = list(Document.objects.for_user(user).filter(employee=employee, is_current=True)
                     .select_related("document_type").order_by("expiry_date"))
    return {"allowed": True, "employee": employee, "documents": documents,
            "can_add": user.has_perm("compliance.add_document") and employee.status != Employee.Status.SEPARATED}
