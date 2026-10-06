from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from .models import Vehicle
from .services import VehicleError
from .usage import OdometerReading


@transaction.atomic
def record_odometer(vehicle, km, on, *, source=OdometerReading.Source.MANUAL, note=""):
    """The one way an odometer changes. Readings must rise with the date, so a late entry (an old fuel
    receipt, say) is accepted when it fits between its neighbours and refused when it does not.
    Cancelled readings are ignored."""
    vehicle = Vehicle.objects.select_for_update().get(pk=vehicle.pk)
    if vehicle.status == Vehicle.Status.SOLD:
        raise VehicleError("A sold vehicle's odometer cannot be changed.")
    if on > timezone.localdate():
        raise VehicleError("The reading date cannot be in the future.")

    readings = vehicle.odometer_readings.filter(is_voided=False)
    before = readings.filter(reading_on__lte=on).order_by("-reading_on", "-id").first()
    after = readings.filter(reading_on__gt=on).order_by("reading_on", "id").first()
    if before and km < before.odometer:
        raise VehicleError(f"The odometer cannot go down: it read {before.odometer} km on "
                           f"{before.reading_on:%d %b %Y}.")
    if after and km > after.odometer:
        raise VehicleError(f"That is higher than the {after.odometer} km already recorded on "
                           f"{after.reading_on:%d %b %Y}.")
    if not before and not after and km < vehicle.odometer:      # no history yet: the stored value is the baseline
        raise VehicleError(f"The odometer cannot go down: it is {vehicle.odometer} km.")

    reading = OdometerReading.objects.create(vehicle=vehicle, reading_on=on, odometer=km, source=source,
                                             note=note)
    # .update() on purpose: the reading itself is the audited record, so every fill-up does not also
    # write a "vehicle changed" audit event.
    Vehicle.objects.filter(pk=vehicle.pk).update(odometer=max(vehicle.odometer, km))
    return reading


@transaction.atomic
def void_reading(reading, reason):
    """Cancel a wrong reading (kept for the record). The vehicle shows the highest reading that is left."""
    reading = OdometerReading.objects.select_for_update().select_related("vehicle").get(pk=reading.pk)
    if reading.is_voided:
        return reading
    reading.is_voided, reading.voided_reason = True, reason
    reading.save()
    top = (OdometerReading.objects.filter(vehicle_id=reading.vehicle_id, is_voided=False)
           .aggregate(top=Max("odometer"))["top"])
    Vehicle.objects.filter(pk=reading.vehicle_id).update(odometer=top or 0)
    return reading
