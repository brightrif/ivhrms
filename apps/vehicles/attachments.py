from django.core.exceptions import ValidationError
from django.db import transaction

from .files import VehicleFile
from .incidents import Accident, Fine
from .loans import VehicleLoan
from .services import VehicleError
from .upkeep import FuelFill, ServiceRecord

Kind = VehicleFile.Kind
MAX_FILES = 30                                    # per entry

# What each kind of entry can hold, and who may look at and change its files.
TARGETS = {
    "fuel": dict(model=FuelFill, field="fuel_fill", allowed=(Kind.RECEIPT, Kind.OTHER),
                 view="vehicles.view_fuelfill", change=("vehicles.change_fuelfill",)),
    "service": dict(model=ServiceRecord, field="service_record", allowed=(Kind.INVOICE, Kind.PHOTO, Kind.OTHER),
                    view="vehicles.view_servicerecord", change=("vehicles.change_servicerecord",)),
    "fine": dict(model=Fine, field="fine", allowed=(Kind.TICKET, Kind.RECEIPT, Kind.OTHER),
                 view="vehicles.view_fine", change=("vehicles.change_fine", "vehicles.pay_fine")),
    "accident": dict(model=Accident, field="accident", allowed=(Kind.POLICE_REPORT, Kind.PHOTO, Kind.OTHER),
                     view="vehicles.view_accident", change=("vehicles.change_accident",)),
    "loan": dict(model=VehicleLoan, field="loan", allowed=(Kind.CONTRACT, Kind.STATEMENT, Kind.OTHER),
                 view="vehicles.view_vehicleloan", change=("vehicles.change_vehicleloan",)),     # Finance and Management only
}


def can_view(user, target_kind):
    return user.has_perm(TARGETS[target_kind]["view"])


def can_change(user, target_kind):
    return any(user.has_perm(p) for p in TARGETS[target_kind]["change"])


def files_of(target):
    return target.files.filter(is_removed=False)


@transaction.atomic
def attach(target_kind, target, *, kind, file, title=""):
    spec = TARGETS[target_kind]
    if getattr(target, "is_voided", False):
        raise VehicleError("A cancelled entry cannot take files.")
    if kind not in spec["allowed"]:
        raise VehicleError("That kind of file does not belong here.")
    if files_of(target).count() >= MAX_FILES:
        raise VehicleError(f"At most {MAX_FILES} files can be attached to one entry.")
    attached = VehicleFile(vehicle_id=target.vehicle_id, kind=kind, title=(title or "").strip(), file=file,
                           **{spec["field"]: target})
    try:
        attached.full_clean(exclude=["company"])           # the same type and size rules as your documents
    except ValidationError as exc:
        raise VehicleError(" ".join(exc.messages)) from exc
    attached.save()
    return attached


@transaction.atomic
def remove(attached, reason):
    """Take a file off the entry. It is kept on disk and in the audit trail, never deleted."""
    reason = (reason or "").strip()
    if not reason:
        raise VehicleError("Give a reason for removing this file.")
    attached = VehicleFile.objects.select_for_update().get(pk=attached.pk)
    if attached.is_removed:
        raise VehicleError("This file is already removed.")
    attached.is_removed, attached.removed_reason = True, reason[:200]
    attached.save()
    return attached
