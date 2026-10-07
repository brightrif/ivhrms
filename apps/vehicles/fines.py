from decimal import Decimal

from django.db import IntegrityError, transaction
from django.utils import timezone

from .fuel import driver_on
from .incidents import Fine
from .services import VehicleError


def resolve_driver(vehicle, on, driver=None):
    """The driver chosen by hand, or else whoever had the vehicle that day (they may have left since)."""
    driver = driver or driver_on(vehicle, on)
    if driver and driver.company_id != vehicle.company_id:
        raise VehicleError(f"{driver.full_name} works for a different company than this vehicle.")
    return driver


@transaction.atomic
def save_fine(fine):
    """Create or edit an unpaid fine. Fines of sold vehicles are still recorded: they arrive late."""
    if fine.pk:
        current = Fine.objects.select_for_update().get(pk=fine.pk)
        if current.is_voided:
            raise VehicleError("A cancelled fine cannot be edited.")
        if current.status == Fine.Status.PAID:
            raise VehicleError("A paid fine cannot be edited. Cancel it and enter it again if it is wrong.")
    if fine.amount is None or fine.amount <= 0:
        raise VehicleError("The amount must be more than zero.")
    if fine.fined_on > timezone.localdate():
        raise VehicleError("The date of the offence cannot be in the future.")
    fine.reference = (fine.reference or "").strip()
    if fine.reference:
        same = Fine.objects.filter(vehicle_id=fine.vehicle_id, reference__iexact=fine.reference, is_voided=False)
        if fine.pk:
            same = same.exclude(pk=fine.pk)
        if same.exists():
            raise VehicleError(f"Fine {fine.reference} is already recorded for this vehicle.")
    fine.driver = resolve_driver(fine.vehicle, fine.fined_on, fine.driver)
    if fine.charged_to_employee and not fine.driver:
        raise VehicleError("Nobody had this vehicle on that day. Choose the driver before charging the fine to them.")
    try:
        fine.save()
    except IntegrityError as exc:
        raise VehicleError("This fine is already recorded for this vehicle.") from exc
    return fine


@transaction.atomic
def pay_fine(fine, paid_on, amount, reference=""):
    fine = Fine.objects.select_for_update().get(pk=fine.pk)
    if fine.is_voided:
        raise VehicleError("A cancelled fine cannot be paid.")
    if fine.status == Fine.Status.PAID:
        raise VehicleError("This fine is already paid.")
    if paid_on > timezone.localdate():
        raise VehicleError("The payment date cannot be in the future.")
    if paid_on < fine.fined_on:
        raise VehicleError(f"It cannot be paid before the offence ({fine.fined_on:%d %b %Y}).")
    if amount <= 0:
        raise VehicleError("The amount paid must be more than zero.")
    fine.status, fine.paid_on, fine.paid_amount = Fine.Status.PAID, paid_on, amount
    fine.payment_reference = (reference or "").strip()
    fine.save()
    return fine


@transaction.atomic
def void_fine(fine, reason):
    """Cancel a fine entered by mistake, dismissed or waived. It stays in the history, marked as cancelled."""
    reason = (reason or "").strip()
    if not reason:
        raise VehicleError("Give a reason for cancelling this fine.")
    fine = Fine.objects.select_for_update().get(pk=fine.pk)
    if fine.is_voided:
        raise VehicleError("This fine is already cancelled.")
    fine.is_voided, fine.voided_reason = True, reason
    fine.save()
    return fine


def unpaid_summary(vehicle):
    rows = list(vehicle.fines.filter(is_voided=False, status=Fine.Status.UNPAID))
    return {"count": len(rows), "total": sum((f.amount for f in rows), Decimal("0"))}
