"""The rules for labor workers. Views and forms call these; they never change the tables directly."""
from datetime import timedelta
from decimal import Decimal

from django.db import transaction

from apps.employees import services as employee_services
from apps.employees.models import Employee

from .models import Contractor, LaborProfile, LaborRate

Engagement, WageBasis = LaborProfile.Engagement, LaborRate.WageBasis


class LaborError(Exception):
    """A rule was broken. The message is written to be shown to the person."""


def _check_terms(*, wage_basis, rate, standard_hours, engagement):
    if wage_basis not in WageBasis.values:
        raise LaborError("Choose a daily wage or a monthly salary.")
    if rate is None or Decimal(rate) <= 0:
        raise LaborError("The rate must be more than zero.")
    if standard_hours is None or not Decimal("1") <= Decimal(standard_hours) <= Decimal("24"):
        raise LaborError("Standard hours per day must be between 1 and 24.")
    if engagement == Engagement.CONTRACTED and wage_basis != WageBasis.DAILY:
        raise LaborError("Contracted workers are paid a daily rate.")


def _check_engagement(employee, engagement, contractor):
    if engagement not in Engagement.values:
        raise LaborError("Choose how this worker is engaged.")
    if employee.worker_type != Employee.WorkerType.LABOR:
        raise LaborError("Only labor workers have a labor profile.")
    if employee.status == Employee.Status.SEPARATED:
        raise LaborError("This worker has left the company.")
    if engagement == Engagement.DIRECT and contractor is not None:
        raise LaborError("A direct employee has no contractor. Choose 'Contracted' or clear the contractor.")
    if contractor is not None:
        if contractor.company_id != employee.company_id:
            raise LaborError("This contractor belongs to another company.")
        if not contractor.is_active:
            raise LaborError(f"{contractor.name} is inactive. Choose another contractor or reactivate it first.")


def _open_profile(employee, *, engagement, contractor, trade, notes, wage_basis, rate, standard_hours,
                  overtime_eligible, effective_from):
    """Create the profile and its first rate for an employee that is already saved."""
    _check_engagement(employee, engagement, contractor)
    _check_terms(wage_basis=wage_basis, rate=rate, standard_hours=standard_hours, engagement=engagement)
    profile = LaborProfile.objects.create(employee=employee, engagement=engagement, contractor=contractor,
                                          trade=trade, notes=notes or "")
    LaborRate.objects.create(profile=profile, wage_basis=wage_basis, rate=rate, standard_hours=standard_hours,
                             overtime_eligible=overtime_eligible, effective_from=effective_from)
    return profile


@transaction.atomic
def create_labor_worker(employee, *, engagement, contractor=None, trade, notes="", wage_basis, rate,
                        standard_hours=Decimal("8"), overtime_eligible=True):
    """Add a brand-new worker: the Employee record (numbered IV-L...) and the labor profile together.
    `employee` is a new, unsaved Employee. Any failure rolls both back, so no number is used up."""
    employee.worker_type = Employee.WorkerType.LABOR
    _check_engagement(employee, engagement, contractor)          # checked first: nothing is saved if it fails
    _check_terms(wage_basis=wage_basis, rate=rate, standard_hours=standard_hours, engagement=engagement)
    try:
        employee, _ = employee_services.create_employee(employee)
    except employee_services.EmployeeError as exc:
        raise LaborError(str(exc)) from exc
    return _open_profile(employee, engagement=engagement, contractor=contractor, trade=trade, notes=notes,
                         wage_basis=wage_basis, rate=rate, standard_hours=standard_hours,
                         overtime_eligible=overtime_eligible, effective_from=employee.joining_date)


@transaction.atomic
def setup_profile(employee, *, engagement, contractor=None, trade, notes="", wage_basis, rate,
                  standard_hours=Decimal("8"), overtime_eligible=True, effective_from=None):
    """Give an existing labor employee a profile (for workers added before this module existed)."""
    if LaborProfile.objects.filter(employee=employee).exists():
        raise LaborError("This worker already has a labor profile.")
    return _open_profile(employee, engagement=engagement, contractor=contractor, trade=trade, notes=notes,
                         wage_basis=wage_basis, rate=rate, standard_hours=standard_hours,
                         overtime_eligible=overtime_eligible, effective_from=effective_from or employee.joining_date)


@transaction.atomic
def update_profile(profile, *, engagement, contractor, trade, notes=""):
    """Change how a worker is engaged, who supplies them, or their trade. Pay terms change through change_rate()."""
    profile = LaborProfile.objects.select_for_update().select_related("employee").get(pk=profile.pk)
    _check_engagement(profile.employee, engagement, contractor)
    current = profile.current_rate
    if engagement == Engagement.CONTRACTED and current and current.wage_basis != WageBasis.DAILY:
        raise LaborError("Contracted workers are paid a daily rate. Change the pay to a daily rate first.")
    profile.engagement, profile.contractor, profile.trade, profile.notes = engagement, contractor, trade, notes or ""
    profile.save()
    return profile


@transaction.atomic
def change_rate(profile, *, effective_from, wage_basis, rate, standard_hours, overtime_eligible):
    """Start new pay terms on a date. The old terms end the day before, so history is never rewritten."""
    profile = LaborProfile.objects.select_for_update().select_related("employee").get(pk=profile.pk)
    if profile.employee.status == Employee.Status.SEPARATED:
        raise LaborError("This worker has left the company.")
    _check_terms(wage_basis=wage_basis, rate=rate, standard_hours=standard_hours, engagement=profile.engagement)
    current = profile.rates.filter(effective_to__isnull=True).first()
    if current and effective_from <= current.effective_from:
        raise LaborError(f"The new rate must start after the current one, which began on "
                         f"{current.effective_from:%d %b %Y}.")
    if current:
        current.effective_to = effective_from - timedelta(days=1)
        current.save()
    return LaborRate.objects.create(profile=profile, wage_basis=wage_basis, rate=rate, standard_hours=standard_hours,
                                    overtime_eligible=overtime_eligible, effective_from=effective_from)


def rate_on(profile, on_date):
    """The pay terms that applied on a date, or None if the worker had none yet."""
    return (profile.rates.filter(effective_from__lte=on_date)
            .exclude(effective_to__lt=on_date).order_by("-effective_from").first())


def unassigned_workers(user):
    """Labor employees who have no profile yet (added before this module, or by the staff form)."""
    return (Employee.objects.for_user(user).filter(worker_type=Employee.WorkerType.LABOR, labor_profile__isnull=True)
            .exclude(status=Employee.Status.SEPARATED).select_related("company"))


def active_workers(contractor):
    return contractor.workers.exclude(employee__status=Employee.Status.SEPARATED).count()


@transaction.atomic
def set_contractor_active(contractor, active):
    contractor = Contractor.objects.select_for_update().get(pk=contractor.pk)
    if not active:
        n = active_workers(contractor)
        if n:
            raise LaborError(f"{n} worker{'s are' if n != 1 else ' is'} still supplied by {contractor.name}. "
                             "Move them to another contractor first.")
    contractor.is_active = active
    contractor.save(update_fields=["is_active"])
    return contractor
