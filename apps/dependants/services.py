from datetime import date

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.db import IntegrityError, transaction

from apps.compliance.models import Document, DocumentType
from apps.employees.models import Employee

from .models import Dependant, DependantAttachment


class DependantError(Exception):
    pass


def _validate(obj):
    try:
        obj.full_clean(exclude=["company"])       # the company is filled in by save()
    except ValidationError as exc:
        raise DependantError(" ".join(exc.messages)) from exc


@transaction.atomic
def add_dependant(user, employee, **fields):
    if employee.status == Employee.Status.SEPARATED:
        raise DependantError("This employee has left the company.")
    dep = Dependant(employee=employee, company_id=employee.company_id, **fields)
    _validate(dep)
    try:
        with transaction.atomic():
            dep.save()
    except IntegrityError as exc:
        raise DependantError("This family member is already recorded for this employee.") from exc
    return dep


@transaction.atomic
def update_dependant(dependant, **fields):
    dependant = Dependant.objects.select_for_update().get(pk=dependant.pk)
    for name, value in fields.items():
        setattr(dependant, name, value)
    _validate(dependant)

    try:
        with transaction.atomic():
            dependant.save()
    except IntegrityError as exc:
        raise DependantError("This family member is already recorded for this employee.") from exc
    return dependant


@transaction.atomic
def deactivate_dependant(dependant, *, left_on: date | None = None):
    """The person is no longer sponsored. Their documents stop being chased (see compliance.current_documents)."""
    dependant = Dependant.objects.select_for_update().get(pk=dependant.pk)
    if not dependant.is_active:
        raise DependantError("This family member is already inactive.")
    dependant.is_active = False
    dependant.left_on = left_on or timezone.localdate()
    dependant.save(update_fields=["is_active", "left_on", "updated_at"])
    return dependant


@transaction.atomic
def add_attachment(user, dependant, kind, file, description=""):
    if kind not in DependantAttachment.Kind.values:
        raise DependantError("Choose what kind of paper this is.")
    att = DependantAttachment(dependant=dependant, kind=kind, file=file, description=description,
                              uploaded_by=user)
    att.company_id, att.employee_id = dependant.company_id, dependant.employee_id
    _validate(att)
    att.save()
    return att


def missing_documents(dependant):
    """Mandatory dependant document types this person has no current document for (the coverage list)."""
    held = Document.objects.filter(dependant=dependant, is_current=True).values_list("document_type_id", flat=True)
    return list(DocumentType.objects.filter(applies_to=DocumentType.AppliesTo.DEPENDANT,
                                            is_active=True, is_mandatory=True).exclude(pk__in=held))
