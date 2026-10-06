from django.db import IntegrityError, transaction
from django.db.models import Max
from django.utils import timezone

from apps.employees.models import Employee

from .models import Vehicle
from .odometer import record_odometer
from .services import VehicleError
from .usage import OdometerReading, VehicleAssignment

LICENCE_CODE = "driving-licence"


class LicenceProblem(VehicleError):
    """The driver has no valid licence on file. HR can still go ahead knowingly (licence_override)."""


def licence_problem(employee, on):
    """None if the employee holds a driving licence valid on `on`, otherwise a sentence saying what is wrong."""
    from apps.compliance.models import Document

    doc = (Document.objects.filter(employee=employee, document_type__code=LICENCE_CODE, is_current=True)
           .order_by("-expiry_date").first())
    if doc is None:
        return f"{employee.full_name} has no driving licence on file."
    if doc.expiry_date < on:
        return f"{employee.full_name}'s driving licence expired on {doc.expiry_date:%d %b %Y}."
    return None


def current_assignment(vehicle):
    return vehicle.assignments.filter(assigned_to__isnull=True).select_related("employee").first()


@transaction.atomic
def assign_vehicle(vehicle, employee, assigned_from, odometer, *, notes="", licence_override=False):
    vehicle = Vehicle.objects.select_for_update().get(pk=vehicle.pk)
    if vehicle.status == Vehicle.Status.SOLD:
        raise VehicleError("A sold vehicle cannot be assigned.")
    if employee.status == Employee.Status.SEPARATED:
        raise VehicleError(f"{employee.full_name} has left the company.")
    if employee.company_id != vehicle.company_id:
        raise VehicleError(f"{employee.full_name} works for a different company than this vehicle.")
    if assigned_from > timezone.localdate():
        raise VehicleError("The hand-over date cannot be in the future.")
    holder = current_assignment(vehicle)
    if holder:
        raise VehicleError(f"This vehicle is already assigned to {holder.employee.full_name}. Return it first.")
    last_end = vehicle.assignments.aggregate(last=Max("assigned_to"))["last"]
    if last_end and assigned_from < last_end:
        raise VehicleError(f"The previous driver returned it on {last_end:%d %b %Y}, "
                           "so the new hand-over cannot be earlier.")
    if not licence_override:
        problem = licence_problem(employee, assigned_from)
        if problem:
            raise LicenceProblem(problem)

    record_odometer(vehicle, odometer, assigned_from, source=OdometerReading.Source.ASSIGNMENT,
                    note=f"Handed over to {employee.full_name}")
    try:
        return VehicleAssignment.objects.create(vehicle=vehicle, employee=employee, assigned_from=assigned_from,
                                                start_odometer=odometer, notes=notes)
    except IntegrityError as exc:
        raise VehicleError("This vehicle is already assigned. Return it first.") from exc


@transaction.atomic
def return_vehicle(assignment, returned_on, odometer, *, notes=""):
    assignment = (VehicleAssignment.objects.select_for_update().select_related("vehicle", "employee")
                  .get(pk=assignment.pk))
    if assignment.assigned_to:
        raise VehicleError("This vehicle has already been returned.")
    if returned_on < assignment.assigned_from:
        raise VehicleError(f"It cannot be returned before it was handed over ({assignment.assigned_from:%d %b %Y}).")
    if returned_on > timezone.localdate():
        raise VehicleError("The return date cannot be in the future.")
    if odometer < assignment.start_odometer:
        raise VehicleError(f"The odometer at return cannot be below the {assignment.start_odometer} km "
                           "at hand-over.")

    record_odometer(assignment.vehicle, odometer, returned_on, source=OdometerReading.Source.ASSIGNMENT,
                    note=f"Returned by {assignment.employee.full_name}")
    assignment.assigned_to, assignment.end_odometer = returned_on, odometer
    if notes:
        assignment.notes = f"{assignment.notes}\n{notes}".strip()
    assignment.save()
    return assignment
