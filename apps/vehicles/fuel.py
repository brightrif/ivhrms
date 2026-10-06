from decimal import Decimal

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .models import Vehicle
from .odometer import record_odometer, void_reading
from .services import VehicleError
from .upkeep import FuelFill
from .usage import OdometerReading


def driver_on(vehicle, day):
    """Whoever had the vehicle on that day, or None."""
    row = (vehicle.assignments.filter(assigned_from__lte=day)
           .filter(Q(assigned_to__isnull=True) | Q(assigned_to__gte=day))
           .select_related("employee").order_by("-assigned_from", "-id").first())
    return row.employee if row else None


@transaction.atomic
def add_fill(vehicle, *, filled_on, litres, cost, km, full_tank=True, station="", notes=""):
    vehicle = Vehicle.objects.select_for_update().get(pk=vehicle.pk)
    if vehicle.status == Vehicle.Status.SOLD:
        raise VehicleError("A sold vehicle cannot take new fuel entries.")
    if litres <= 0:
        raise VehicleError("Litres must be more than zero.")
    if cost < 0:
        raise VehicleError("The cost cannot be negative.")
    if filled_on > timezone.localdate():
        raise VehicleError("The fill-up date cannot be in the future.")
    reading = record_odometer(vehicle, km, filled_on, source=OdometerReading.Source.FUEL, note=f"Fuel {litres} L")
    return FuelFill.objects.create(
        vehicle=vehicle, driver=driver_on(vehicle, filled_on), filled_on=filled_on, litres=litres, cost=cost,
        odometer=km, full_tank=full_tank, station=station, notes=notes, reading=reading)


@transaction.atomic
def void_fill(fill, reason):
    reason = (reason or "").strip()
    if not reason:
        raise VehicleError("Give a reason for cancelling this entry.")
    fill = FuelFill.objects.select_for_update().get(pk=fill.pk)
    if fill.is_voided:
        raise VehicleError("This entry is already cancelled.")
    if fill.reading_id:
        void_reading(fill.reading, f"Fuel entry cancelled: {reason}")
    fill.is_voided, fill.voided_reason = True, reason
    fill.save()
    return fill


def fuel_stats(vehicle, today=None):
    """Consumption from full tank to full tank: the fuel added since the previous full tank (partial fills
    included) is what the distance between the two cost. Cancelled entries are listed but never counted."""
    today = today or timezone.localdate()
    fills = list(vehicle.fuel_fills.select_related("driver").order_by("filled_on", "odometer", "id"))
    prev_full, litres, cost = None, Decimal("0"), Decimal("0")
    total_km, total_litres, total_cost = 0, Decimal("0"), Decimal("0")
    year_litres, year_cost = Decimal("0"), Decimal("0")
    for f in fills:
        f.km_per_litre = f.cost_per_km = None
        if f.is_voided:
            continue
        if (today - f.filled_on).days <= 365:
            year_litres += f.litres
            year_cost += f.cost
        if prev_full is None and not f.full_tank:
            continue                                  # before the first full tank there is nothing to measure from
        if prev_full is not None:
            litres += f.litres
            cost += f.cost
        if f.full_tank:
            if prev_full is not None:
                km = f.odometer - prev_full.odometer
                if km > 0 and litres > 0:
                    f.km_per_litre, f.cost_per_km = Decimal(km) / litres, cost / km
                    total_km += km
                    total_litres += litres
                    total_cost += cost
            prev_full, litres, cost = f, Decimal("0"), Decimal("0")
    return {
        "rows": list(reversed(fills)),                # newest first
        "avg_kmpl": Decimal(total_km) / total_litres if total_litres else None,
        "cost_per_km": total_cost / total_km if total_km else None,
        "litres_12m": year_litres, "cost_12m": year_cost,
    }
