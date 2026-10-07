from django.db import transaction
from django.utils import timezone

from .fines import resolve_driver
from .incidents import Accident
from .services import VehicleError
from .upkeep import ServiceRecord


@transaction.atomic
def save_accident(accident):
    """Create or edit an accident. Accidents of sold vehicles are still recorded: reports and claims come late."""
    if accident.pk:
        if Accident.objects.select_for_update().get(pk=accident.pk).is_voided:
            raise VehicleError("A cancelled accident cannot be edited.")
    if accident.occurred_on > timezone.localdate():
        raise VehicleError("The date of the accident cannot be in the future.")
    if (accident.insurance_recovered or 0) < 0:
        raise VehicleError("The amount recovered cannot be negative.")
    if accident.claim_status == Accident.Claim.NONE and accident.insurance_recovered:
        raise VehicleError("Money recovered from insurance needs a claim: set the claim status.")
    accident.driver = resolve_driver(accident.vehicle, accident.occurred_on, accident.driver)
    accident.save()
    return accident


@transaction.atomic
def close_accident(accident, closed_on):
    accident = Accident.objects.select_for_update().get(pk=accident.pk)
    if accident.is_voided:
        raise VehicleError("A cancelled accident cannot be closed.")
    if accident.status == Accident.Status.CLOSED:
        raise VehicleError("This accident is already closed.")
    if closed_on > timezone.localdate():
        raise VehicleError("The closing date cannot be in the future.")
    if closed_on < accident.occurred_on:
        raise VehicleError(f"It cannot be closed before it happened ({accident.occurred_on:%d %b %Y}).")
    accident.status, accident.closed_on = Accident.Status.CLOSED, closed_on
    accident.save()
    return accident


@transaction.atomic
def reopen_accident(accident):
    accident = Accident.objects.select_for_update().get(pk=accident.pk)
    if accident.is_voided:
        raise VehicleError("A cancelled accident cannot be reopened.")
    if accident.status == Accident.Status.OPEN:
        raise VehicleError("This accident is already open.")
    accident.status, accident.closed_on = Accident.Status.OPEN, None
    accident.save()
    return accident


@transaction.atomic
def void_accident(accident, reason):
    reason = (reason or "").strip()
    if not reason:
        raise VehicleError("Give a reason for cancelling this accident.")
    accident = Accident.objects.select_for_update().get(pk=accident.pk)
    if accident.is_voided:
        raise VehicleError("This accident is already cancelled.")
    accident.is_voided, accident.voided_reason = True, reason
    accident.save()
    return accident


@transaction.atomic
def link_repair(accident, record):
    """Attach a repair logged in Maintenance, so the accident shows what it cost to put right."""
    accident = Accident.objects.select_for_update().get(pk=accident.pk)
    record = ServiceRecord.objects.get(pk=record.pk)
    if accident.is_voided:
        raise VehicleError("A cancelled accident cannot take repairs.")
    if record.vehicle_id != accident.vehicle_id:
        raise VehicleError("That repair belongs to a different vehicle.")
    if record.kind != ServiceRecord.Kind.REPAIR or record.is_voided:
        raise VehicleError("Only a repair that has not been cancelled can be linked.")
    if record.accidents.exclude(pk=accident.pk).exists():
        raise VehicleError("That repair is already linked to another accident.")
    accident.repairs.add(record)
    return accident


@transaction.atomic
def unlink_repair(accident, record):
    accident = Accident.objects.select_for_update().get(pk=accident.pk)
    accident.repairs.remove(record)
    return accident
