"""Fixing a labor worker's details: the worker card, the current pay line, or everything at once."""
from decimal import Decimal

from django.db import transaction

from apps.employees import joining
from apps.employees.models import Employee

from . import services
from .conversion import _NOT_TERMS
from .models import LaborProfile, LaborRate
from .services import Engagement, LaborError, WageBasis


@transaction.atomic
def edit_worker(employee, *, first_name, last_name, nationality, phone, joining_date):
    """The Worker card. Returns notes to show the person."""
    employee = Employee.objects.select_for_update().get(pk=employee.pk)
    if employee.status == Employee.Status.SEPARATED:
        raise LaborError("This worker has left the company.")
    employee.first_name, employee.last_name = first_name, last_name
    employee.nationality, employee.phone = nationality, phone
    employee.save()
    try:
        return joining.change_joining_date(employee, joining_date)
    except joining.JoiningDateError as exc:
        raise LaborError(str(exc)) from exc


@transaction.atomic
def correct_rate(profile, *, terms, engagement=None):
    """Fix a mistake in the current pay line, in place: no new history row. A real change of pay, from a later
    date, is change_rate()."""
    profile = LaborProfile.objects.select_for_update().select_related("employee").get(pk=profile.pk)
    if profile.employee.status == Employee.Status.SEPARATED:
        raise LaborError("This worker has left the company.")
    current = profile.rates.select_for_update().filter(effective_to__isnull=True).first()
    if current is None:
        raise LaborError("There are no current pay terms to correct. Use Change pay.")
    names = {f.name for f in LaborRate._meta.concrete_fields} - _NOT_TERMS
    terms = {k: v for k, v in terms.items() if k in names}
    if terms.get("rate") is None or Decimal(terms["rate"]) <= 0:
        raise LaborError("The rate must be more than zero.")
    if (engagement or profile.engagement) == Engagement.CONTRACTED and terms.get("wage_basis") != WageBasis.DAILY:
        raise LaborError("Contracted workers are paid a daily rate.")
    for name, value in terms.items():
        setattr(current, name, value)
    current.save()
    return current


@transaction.atomic
def edit_everything(profile, *, worker, joining_date, engagement, contractor, trade, notes, terms):
    """All three cards in one go. Returns notes to show the person."""
    to_show = edit_worker(profile.employee, joining_date=joining_date, **worker)
    if profile.current_rate:
        correct_rate(profile, terms=terms, engagement=engagement)
    services.update_profile(profile, engagement=engagement, contractor=contractor, trade=trade, notes=notes)
    return to_show