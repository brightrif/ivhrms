"""Fines charged to the driver: what is still to be recovered from them. Payroll will take this over later."""
from decimal import Decimal
from types import SimpleNamespace

from django.db import transaction
from django.utils import timezone

from .incidents import Fine
from .services import VehicleError


@transaction.atomic
def mark_recovered(fine, recovered_on, note=""):
    fine = Fine.objects.select_for_update().get(pk=fine.pk)
    if fine.is_voided:
        raise VehicleError("A cancelled fine cannot be recovered.")
    if not fine.charged_to_employee or not fine.driver_id:
        raise VehicleError("This fine is not charged to a driver.")
    if fine.recovered_on:
        raise VehicleError("This fine is already recovered.")
    if recovered_on > timezone.localdate():
        raise VehicleError("The recovery date cannot be in the future.")
    if recovered_on < fine.fined_on:
        raise VehicleError(f"It cannot be recovered before the offence ({fine.fined_on:%d %b %Y}).")
    fine.recovered_on, fine.recovered_note = recovered_on, (note or "").strip()
    fine.save()
    return fine


@transaction.atomic
def undo_recovery(fine, reason):
    reason = (reason or "").strip()
    if not reason:
        raise VehicleError("Give a reason for undoing this.")
    fine = Fine.objects.select_for_update().get(pk=fine.pk)
    if not fine.recovered_on:
        raise VehicleError("This fine has not been recovered.")
    fine.recovered_on, fine.recovered_note = None, f"Recovery undone: {reason}"[:200]
    fine.save()
    return fine


def to_recover(user):
    """Open fines charged to drivers, grouped by person, largest first."""
    fines = (Fine.objects.for_user(user).filter(is_voided=False, charged_to_employee=True, recovered_on__isnull=True,
                                                driver__isnull=False)
             .select_related("vehicle", "driver").order_by("fined_on"))
    people = {}
    for f in fines:
        people.setdefault(f.driver_id, SimpleNamespace(employee=f.driver, fines=[], total=Decimal("0")))
        people[f.driver_id].fines.append(f)
        people[f.driver_id].total += f.display_amount
    return sorted(people.values(), key=lambda p: (-p.total, p.employee.full_name))


def recovered(user, limit=50):
    return list(Fine.objects.for_user(user).filter(is_voided=False, recovered_on__isnull=False)
                .select_related("vehicle", "driver").order_by("-recovered_on")[:limit])
