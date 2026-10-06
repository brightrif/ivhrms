from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

from apps.audit.registry import audited
from apps.core.models import BaseModel
from apps.core.scoping import CompanyQuerySet


def normalise_plate(value):
    """Upper case with single spaces: '  ab  123 ' -> 'AB 123'."""
    return " ".join((value or "").upper().split())


def normalise_chassis(value):
    return (value or "").strip().upper()


@audited(module="vehicles", company="company_id")
class Vehicle(BaseModel):
    """One vehicle the firm owns, finances or leases. Sold vehicles are kept as a record, never deleted."""

    class Kind(models.TextChoices):
        CAR = "car", "Car"
        PICKUP = "pickup", "Pickup"
        VAN = "van", "Van"
        TRUCK = "truck", "Truck"
        MOTORCYCLE = "motorcycle", "Motorcycle"
        OTHER = "other", "Other"

    class FuelType(models.TextChoices):
        PETROL = "petrol", "Petrol"
        DIESEL = "diesel", "Diesel"
        HYBRID = "hybrid", "Hybrid"
        ELECTRIC = "electric", "Electric"

    class Ownership(models.TextChoices):
        OWNED = "owned", "Owned"
        LOAN = "loan", "Bought with a loan"
        LEASED = "leased", "Leased"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        WORKSHOP = "workshop", "In workshop"
        SOLD = "sold", "Sold"

    # Which colour the shared status_badge tag should use for each status.
    BADGE_STATES = {"active": "active", "workshop": "on_notice", "sold": "separated"}

    company = models.ForeignKey("organization.Company", on_delete=models.PROTECT, related_name="vehicles")
    plate_number = models.CharField(max_length=20)
    kind = models.CharField(max_length=12, choices=Kind.choices, default=Kind.CAR)
    make = models.CharField(max_length=60)
    model = models.CharField(max_length=60, blank=True)
    year = models.PositiveSmallIntegerField(null=True, blank=True, validators=[MinValueValidator(1950)])
    colour = models.CharField(max_length=30, blank=True)
    chassis_number = models.CharField("Chassis (VIN) number", max_length=30, blank=True)
    fuel_type = models.CharField(max_length=10, choices=FuelType.choices, default=FuelType.PETROL)
    ownership = models.CharField(max_length=10, choices=Ownership.choices, default=Ownership.OWNED)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    # Kilometres. Only ever goes up; later phases update it through one service function.
    odometer = models.PositiveIntegerField("Odometer (km)", default=0)
    sold_on = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)

    objects = CompanyQuerySet.as_manager()

    class Meta:
        ordering = ["company", "plate_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "plate_number"], condition=~models.Q(status="sold"),
                name="uniq_unsold_vehicle_plate",
                violation_error_message="This company already has an unsold vehicle with this plate number."),
            models.UniqueConstraint(
                fields=["chassis_number"], condition=~models.Q(chassis_number="") & ~models.Q(status="sold"),
                name="uniq_unsold_vehicle_chassis",
                violation_error_message="A vehicle with this chassis number is already registered."),
            models.CheckConstraint(
                condition=models.Q(sold_on__isnull=False) | ~models.Q(status="sold"),
                name="vehicle_sold_has_date"),
        ]

    @property
    def badge_state(self):
        return self.BADGE_STATES[self.status]

    @property
    def description(self):
        return " ".join(str(p) for p in (self.make, self.model, self.year) if p)

    def clean(self):
        if self.year and self.year > timezone.localdate().year + 1:
            raise ValidationError({"year": "The model year cannot be in the future."})

    def save(self, *args, **kwargs):
        self.plate_number = normalise_plate(self.plate_number)
        self.chassis_number = normalise_chassis(self.chassis_number)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.plate_number} - {self.description}".rstrip(" -")
