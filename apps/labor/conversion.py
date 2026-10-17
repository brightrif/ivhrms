"""Moving a person between labor and staff (a promotion, or a move to site work).

It stays the same person: documents, leave and history carry over. What changes is the type, the employee number
series and the labor side (site allocations and pay terms). Labor history is kept, so past months price as they did."""
import re
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from apps.employees import numbering
from apps.employees import services as employee_services
from apps.employees.models import Employee, EmploymentRecord

from . import deployment
from .models import LaborProfile, LaborRate
from .services import Engagement, LaborError, WageBasis, _check_engagement
from .timesheet import TimeEntry

Type = Employee.WorkerType


def _check_can_change(employee, expected, effective_from):
    if employee.status == Employee.Status.SEPARATED:
        raise LaborError(f"{employee.full_name} has left the company.")
    if employee.worker_type != expected:
        raise LaborError(f"{employee.full_name} is already {'labor' if expected == Type.STAFF else 'staff'}.")
    if effective_from > timezone.localdate():
        raise LaborError("Future-dated changes are not supported yet. Use today's date or earlier.")
    if effective_from < employee.joining_date:
        raise LaborError(f"{employee.full_name} joined on {employee.joining_date:%d %b %Y}; "
                         "the change cannot start earlier.")
    last = employee.history.order_by("-effective_from").first()
    if last and effective_from <= last.effective_from:
        raise LaborError(f"{employee.full_name}'s record was last changed on {last.effective_from:%d %b %Y}. "
                         "Choose a later date.")


def _note(old_type, old_no, new_type, new_no, extra):
    text = f"Changed from {old_type} ({old_no}) to {new_type} ({new_no})"
    if (extra or "").strip():
        text += f". {extra.strip()}"
    return text[:255]

def previous_number(employee, worker_type):
    """The number this person held the last time they were `worker_type`, read from the note an earlier change wrote
    in their history. None if there is none, or if someone else holds it now."""
    word = "labor" if worker_type == Type.LABOR else "staff"
    pattern = re.compile(rf"^Changed from {word} \((?P<no>[^)]+)\) to ")
    for record in employee.history.order_by("-effective_from"):
        match = pattern.match(record.remarks or "")
        if match:
            number = match.group("no")
            taken = (Employee.objects.filter(company_id=employee.company_id, employee_no=number)
                     .exclude(pk=employee.pk).exists())
            return None if taken else number
    return None


def _choose_number(employee, worker_type, choice):
    """'previous' gives back the number they had before, 'keep' leaves the current one, 'new' takes the next."""
    if choice == "keep":
        return employee.employee_no
    if choice == "previous":
        number = previous_number(employee, worker_type)
        if not number:
            raise LaborError("Their earlier number is not available. Choose a new number.")
        return number
    return numbering.next_number(employee.company, worker_type)

@transaction.atomic
def to_staff(employee, *, effective_from, designation, department=None, reporting_manager=None,
             employment_type=None, number="new", remarks=""):
    """A labor worker becomes a staff member. `effective_from` is their first day as staff."""
    employee = Employee.objects.select_for_update().select_related("company").get(pk=employee.pk)
    _check_can_change(employee, Type.LABOR, effective_from)
    if designation is None:
        raise LaborError("Choose a designation.")
    if TimeEntry.objects.filter(employee=employee, date__gte=effective_from).exists():
        raise LaborError("Site hours are already recorded from that date. Choose a later date.")

    last_day = effective_from - timedelta(days=1)
    for allocation in deployment.open_allocations(employee):
        deployment.leave_project(allocation, last_day=last_day)
    profile = LaborProfile.objects.select_for_update().filter(employee=employee).first()
    if profile:
        deployment.set_shared(profile, False)
        current = profile.rates.filter(effective_to__isnull=True).first()
        if current:
            if effective_from <= current.effective_from:
                raise LaborError(f"Their pay terms started on {current.effective_from:%d %b %Y}. "
                                 "Choose a later date.")
            current.effective_to = last_day
            current.save()

    old_no = employee.employee_no
    employee.employee_no = _choose_number(employee, Type.STAFF, number)
    employee.worker_type = Type.STAFF
    employee.save()
    changes = {"designation": designation}
    if department:
        changes["department"] = department
    if reporting_manager:
        changes["reporting_manager"] = reporting_manager
    if employment_type:
        changes["employment_type"] = employment_type
    try:
        employee_services.record_assignment(
            employee, effective_from=effective_from, reason=EmploymentRecord.Reason.PROMOTION,
            remarks=_note("labor", old_no, "staff", employee.employee_no, remarks), **changes)
    except ValueError as exc:
        raise LaborError(str(exc)) from exc
    return employee


_NOT_TERMS = {"id", "created_at", "updated_at", "created_by", "updated_by", "profile", "effective_from", "effective_to"}


def _terms_of(rate):
    """Every pay term on a LaborRate row, so terms added later come along too, without its dates."""
    return {f.name: getattr(rate, f.name) for f in LaborRate._meta.concrete_fields if f.name not in _NOT_TERMS}


def earlier_rate(employee):
    """The pay they had the last time they were labor, or None if they never were."""
    profile = LaborProfile.objects.filter(employee=employee).first()
    return profile.rates.order_by("-effective_from").first() if profile else None


@transaction.atomic
def to_labor(employee, *, effective_from, engagement, trade, contractor=None, notes="", terms=None,
             number="new", remarks=""):
    """A staff member moves to site work. `effective_from` is their first day as labor. `terms` is a dict of pay terms
    (wage_basis, rate, standard_hours, ...). Left out, they come back on the pay they had the last time they were labor."""
    employee = Employee.objects.select_for_update().select_related("company").get(pk=employee.pk)
    _check_can_change(employee, Type.STAFF, effective_from)
    old_no = employee.employee_no
    employee.worker_type = Type.LABOR              # in memory only, until the checks below pass
    _check_engagement(employee, engagement, contractor)
    profile = LaborProfile.objects.select_for_update().filter(employee=employee).first()
    if terms is None:
        earlier = profile.rates.order_by("-effective_from").first() if profile else None
        if earlier is None:
            raise LaborError("Enter the pay terms.")
        terms = _terms_of(earlier)
    names = {f.name for f in LaborRate._meta.concrete_fields} - _NOT_TERMS
    terms = {k: v for k, v in terms.items() if k in names}
    if terms.get("rate") is None or Decimal(terms["rate"]) <= 0:
        raise LaborError("The rate must be more than zero.")
    if engagement == Engagement.CONTRACTED and terms.get("wage_basis") != WageBasis.DAILY:
        raise LaborError("Contracted workers are paid a daily rate.")
    if profile:
        if profile.rates.filter(effective_to__isnull=True).exists():
            raise LaborError("This person still has open labor pay terms.")
        ended = profile.rates.aggregate(last=Max("effective_to"))["last"]
        if ended and effective_from <= ended:
            raise LaborError(f"Their earlier labor pay terms ran until {ended:%d %b %Y}. Choose a later date.")

    employee.employee_no = _choose_number(employee, Type.LABOR, number)
    employee.save()
    try:
        employee_services.record_assignment(
            employee, effective_from=effective_from, reason=EmploymentRecord.Reason.CONTRACT_CHANGE,
            remarks=_note("staff", old_no, "labor", employee.employee_no, remarks))
    except ValueError as exc:
        raise LaborError(str(exc)) from exc
    if profile:                                    # the earlier labor record is reused, with a new pay row
        profile.engagement, profile.contractor, profile.trade, profile.notes = engagement, contractor, trade, notes or ""
        profile.save()
    else:
        profile = LaborProfile.objects.create(employee=employee, engagement=engagement, contractor=contractor,
                                              trade=trade, notes=notes or "")
    LaborRate.objects.create(profile=profile, effective_from=effective_from, **terms)
    return employee