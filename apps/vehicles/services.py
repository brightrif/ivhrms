from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import Vehicle, normalise_chassis, normalise_plate
from .usage import OdometerReading

class VehicleError(Exception):
    pass


def plate_in_use(company_id, plate, exclude_pk=None):
    """Plates are unique per company among vehicles that are not sold (a plate can be reused after a sale)."""
    qs = (Vehicle.objects.filter(company_id=company_id, plate_number=normalise_plate(plate))
          .exclude(status=Vehicle.Status.SOLD))
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)
    return qs.exists()


def chassis_in_use(chassis, exclude_pk=None):
    chassis = normalise_chassis(chassis)
    if not chassis:
        return False
    qs = Vehicle.objects.filter(chassis_number=chassis).exclude(status=Vehicle.Status.SOLD)
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)
    return qs.exists()


def _check_unique(vehicle):
    if vehicle.status != Vehicle.Status.SOLD:
        if plate_in_use(vehicle.company_id, vehicle.plate_number, vehicle.pk):
            raise VehicleError("This company already has an unsold vehicle with this plate number.")
        if chassis_in_use(vehicle.chassis_number, vehicle.pk):
            raise VehicleError("A vehicle with this chassis number is already registered.")


@transaction.atomic
def create_vehicle(user, vehicle):
    _check_unique(vehicle)
    try:
        vehicle.save()
    except IntegrityError as exc:
        raise VehicleError("This plate or chassis number is already registered.") from exc
    if vehicle.odometer:
        OdometerReading.objects.create(vehicle=vehicle, reading_on=timezone.localdate(),
                                       odometer=vehicle.odometer, source=OdometerReading.Source.INITIAL)
    return vehicle


@transaction.atomic
def update_vehicle(vehicle):
    _check_unique(vehicle)
    try:
        vehicle.save()
    except IntegrityError as exc:
        raise VehicleError("This plate or chassis number is already registered.") from exc
    return vehicle


@transaction.atomic
def mark_sold(vehicle, sold_on):
    """Retire a vehicle. Its record and documents stay; its documents stop sending alerts."""
    vehicle = Vehicle.objects.select_for_update().get(pk=vehicle.pk)
    if vehicle.status == Vehicle.Status.SOLD:
        raise VehicleError("This vehicle is already marked as sold.")
    if sold_on > timezone.localdate():
        raise VehicleError("The sale date cannot be in the future.")
    vehicle.status, vehicle.sold_on = Vehicle.Status.SOLD, sold_on
    vehicle.save()
    return vehicle
