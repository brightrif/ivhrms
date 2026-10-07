import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.mail import send_mass_mail
from django.db import transaction
from django.urls import NoReverseMatch, reverse

from apps.core import approvals
from apps.core.models import ApprovalRequest
from apps.employees.models import Employee

from . import assignments
from .custody import CustodyRequest, LeaverAlertLog
from .models import Vehicle
from .services import VehicleError
from .usage import VehicleAssignment

logger = logging.getLogger(__name__)
FLOW_CODE = "vehicles.custody"
Decision, Status = CustodyRequest.Decision, CustodyRequest.Status


# ---------------------------------------------------------------- one-step hand-over

@transaction.atomic
def hand_over(vehicle, employee, on, odometer, *, notes="", licence_override=False):
    """Take the vehicle from its current driver and give it to someone else, in one step and on the same date.
    If the new driver cannot have it (no licence, say), nothing changes."""
    vehicle = Vehicle.objects.select_for_update().get(pk=vehicle.pk)
    current = assignments.current_assignment(vehicle)
    if current and current.employee_id == employee.pk:
        raise VehicleError(f"The vehicle is already with {employee.full_name}.")
    if current:
        assignments.return_vehicle(current, on, odometer, notes=f"Handed over to {employee.full_name}")
    return assignments.assign_vehicle(vehicle, employee, on, odometer, notes=notes, licence_override=licence_override)


# ---------------------------------------------------------------- workshop

@transaction.atomic
def send_to_workshop(vehicle):
    vehicle = Vehicle.objects.select_for_update().get(pk=vehicle.pk)
    if vehicle.status != Vehicle.Status.ACTIVE:
        raise VehicleError("Only an active vehicle can be sent to the workshop.")
    vehicle.status = Vehicle.Status.WORKSHOP
    vehicle.save(update_fields=["status"])
    return vehicle


@transaction.atomic
def back_from_workshop(vehicle):
    vehicle = Vehicle.objects.select_for_update().get(pk=vehicle.pk)
    if vehicle.status != Vehicle.Status.WORKSHOP:
        raise VehicleError("This vehicle is not in the workshop.")
    vehicle.status = Vehicle.Status.ACTIVE
    vehicle.save(update_fields=["status"])
    return vehicle


# ---------------------------------------------------------------- custody decisions (approval workflow)

def current_decision(assignment):
    """The latest request about this holding that was not cancelled, or None."""
    return assignment.custody_requests.exclude(status=Status.CANCELLED).select_related("new_driver").order_by("-id").first()


@transaction.atomic
def request_custody_decision(assignment, decision, *, requested_by, new_driver=None, planned_on=None, reason=""):
    assignment = (VehicleAssignment.objects.select_for_update().select_related("vehicle", "employee")
                  .get(pk=assignment.pk))
    emp, vehicle = assignment.employee, assignment.vehicle
    if assignment.assigned_to:
        raise VehicleError(f"{vehicle.plate_number} is no longer with {emp.full_name}.")
    if vehicle.status == Vehicle.Status.SOLD:
        raise VehicleError("A sold vehicle has nothing to decide.")
    if emp.status == Employee.Status.ACTIVE:
        raise VehicleError(f"{emp.full_name} is still an active employee. A custody decision is for someone who is "
                           "on notice or has left. To lend the vehicle meanwhile, use Hand over.")
    if decision not in Decision.values:
        raise VehicleError("Choose what should happen to the vehicle.")
    reason = (reason or "").strip()
    if decision == Decision.HANDOVER:
        if new_driver is None:
            raise VehicleError("Choose who it is handed over to.")
        if new_driver.pk == emp.pk:
            raise VehicleError("That is the person who holds it now.")
        if new_driver.status != Employee.Status.ACTIVE or new_driver.company_id != vehicle.company_id:
            raise VehicleError(f"{new_driver.full_name} must be an active employee of {vehicle.company.name}.")
    else:
        new_driver = None
    if decision == Decision.KEEP and not reason:
        raise VehicleError(f"Give the reason {emp.full_name} keeps the vehicle.")
    if assignment.custody_requests.filter(status=Status.PENDING).exists():
        raise VehicleError("A decision for this vehicle is already waiting for approval.")

    request = CustodyRequest.objects.create(vehicle=vehicle, assignment=assignment, employee=emp, decision=decision,
                                            new_driver=new_driver, planned_on=planned_on, reason=reason)
    try:
        approvals.submit(FLOW_CODE, request, employee=emp, requested_by=requested_by)
    except approvals.ApprovalError as exc:
        raise VehicleError(str(exc)) from exc               # raising inside atomic() rolls the request back
    return request


@transaction.atomic
def finalize(approval):
    """Called when the approval finishes, whatever the outcome. Idempotent."""
    request = CustodyRequest.objects.select_for_update().get(pk=approval.object_id)
    if request.status != Status.PENDING:
        return request
    request.status = {ApprovalRequest.Status.APPROVED: Status.APPROVED,
                      ApprovalRequest.Status.REJECTED: Status.REJECTED}.get(approval.status, Status.CANCELLED)
    request.save(update_fields=["status"])
    return request


@transaction.atomic
def cancel_request(request, user):
    ct = ContentType.objects.get_for_model(CustodyRequest)
    approval = ApprovalRequest.objects.filter(content_type=ct, object_id=str(request.pk),
                                              status=ApprovalRequest.Status.PENDING).first()
    if approval is None:
        raise VehicleError("This request is not waiting for approval.")
    try:
        approvals.cancel(approval.pk, user)              # finishes the approval, which cancels the request
    except approvals.ApprovalError as exc:
        raise VehicleError(str(exc)) from exc
    return CustodyRequest.objects.get(pk=request.pk)


# ---------------------------------------------------------------- telling HR

def hr_addresses(company_id):
    User = get_user_model()
    users = User.objects.filter(is_active=True, groups__name="HR", company_access__company_id=company_id).distinct()
    return sorted({u.email for u in users if u.email})


def _link(name, pk):
    try:
        return getattr(settings, "HRMS_BASE_URL", "").rstrip("/") + reverse(name, args=[pk])
    except NoReverseMatch:
        return ""


def build_message(assignment, state, decision):
    emp, vehicle = assignment.employee, assignment.vehicle
    plate = vehicle.plate_number
    if state == "notice":
        subject = f"[Vehicles] {emp.full_name} is on notice and holds {plate}"
        lines = [f"{emp.full_name} is on notice and still holds vehicle {plate}.",
                 "Decide what happens to it: hand it over, let them keep it, or have it returned."]
        link = _link("web:vehicle_custody_request", vehicle.pk)
    else:
        subject = f"[Vehicles] {emp.full_name} has left and {plate} is still assigned to them"
        lines = [f"{emp.full_name} has left the company and vehicle {plate} is still assigned to them."]
        if decision and decision.status == Status.APPROVED:
            lines.append({Decision.KEEP: f"It was approved to stay with them: {decision.reason}",
                          Decision.HANDOVER: f"It was approved to be handed over to {decision.new_driver.full_name if decision.new_driver else 'someone else'}.",
                          Decision.RETURN: "It was approved to be returned to the company."}[decision.decision])
        lines.append("Record the hand-over or the return when it happens, so the history stays right.")
        link = _link("web:vehicle_detail", vehicle.pk)
    if link:
        lines += ["", f"Open in Ivhrms: {link}"]
    lines += ["", "This is an automatic notice from Ivhrms."]
    return subject, "\n".join(lines)


def run_leaver_scan(today=None):
    """Tell HR once when a vehicle's holder goes on notice, and once more when they have left. Nothing is blocked.
    A holder on notice whose vehicle already has a decision waiting or approved is not chased. Safe to re-run."""
    stats = {"checked": 0, "alerts": 0, "emails": 0, "errors": 0}
    rows = (VehicleAssignment.objects.filter(assigned_to__isnull=True, vehicle__company__is_active=True,
                                             employee__status__in=[Employee.Status.ON_NOTICE, Employee.Status.SEPARATED])
            .exclude(vehicle__status=Vehicle.Status.SOLD).select_related("vehicle", "employee"))
    for a in rows:
        state = "left" if a.employee.status == Employee.Status.SEPARATED else "notice"
        decision = current_decision(a)
        if state == "notice" and decision and decision.status in (Status.PENDING, Status.APPROVED):
            continue
        stats["checked"] += 1
        if a.leaver_alerts.filter(state=state).exists():
            continue
        try:
            with transaction.atomic():
                addresses = hr_addresses(a.company_id)
                sent = 0
                if addresses:
                    subject, body = build_message(a, state, decision)
                    sent = send_mass_mail([(subject, body, None, [addr]) for addr in addresses])
                LeaverAlertLog.objects.create(assignment=a, state=state, recipients=sent)
            stats["alerts"] += 1
            stats["emails"] += sent
        except Exception:                           # one failing row must not stop the others
            logger.exception("Leaver notice failed for assignment %s", a.pk)
            stats["errors"] += 1
    return stats
