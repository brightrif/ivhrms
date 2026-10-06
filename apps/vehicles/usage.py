"""Who drives a vehicle, and what its odometer has read. Kept apart from models.py so that file stays small;
models.py imports these two classes at its bottom so Django registers them."""
from django.db import models

from apps.audit.registry import audited
from apps.core.models import BaseModel
from apps.core.scoping import CompanyQuerySet


@audited(module="vehicles", company="company_id", subject="employee_id")
class VehicleAssignment(BaseModel):
    """Who had a vehicle, from when to when. The one row with no end date is the current driver."""
    company = models.ForeignKey("organization.Company", on_delete=models.PROTECT, related_name="+",
                                editable=False)
    vehicle = models.ForeignKey("vehicles.Vehicle", on_delete=models.PROTECT, related_name="assignments")
    employee = models.ForeignKey("employees.Employee", on_delete=models.PROTECT,
                                 related_name="vehicle_assignments")
    assigned_from = models.DateField()
    assigned_to = models.DateField(null=True, blank=True)
    start_odometer = models.PositiveIntegerField("Odometer at hand-over (km)")
    end_odometer = models.PositiveIntegerField("Odometer at return (km)", null=True, blank=True)
    notes = models.TextField(blank=True)

    objects = CompanyQuerySet.as_manager()

    class Meta:
        ordering = ["-assigned_from", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["vehicle"], condition=models.Q(assigned_to__isnull=True),
                name="one_open_assignment_per_vehicle",
                violation_error_message="This vehicle is already assigned. Return it first."),
            models.CheckConstraint(
                condition=models.Q(assigned_to__isnull=True) | models.Q(assigned_to__gte=models.F("assigned_from")),
                name="assignment_ends_after_it_starts"),
            models.CheckConstraint(
                condition=models.Q(end_odometer__isnull=True) | models.Q(end_odometer__gte=models.F("start_odometer")),
                name="assignment_end_km_not_below_start"),
        ]
        indexes = [models.Index(fields=["employee", "assigned_to"])]

    def save(self, *args, **kwargs):
        self.company_id = self.vehicle.company_id          # the company follows the vehicle
        super().save(*args, **kwargs)

    @property
    def is_open(self):
        return self.assigned_to is None

    @property
    def km_driven(self):
        return None if self.end_odometer is None else self.end_odometer - self.start_odometer

    def __str__(self):
        return f"{self.vehicle.plate_number} - {self.employee.full_name} from {self.assigned_from}"


@audited(module="vehicles", company="company_id")
class OdometerReading(BaseModel):
    """Every reading ever taken. The vehicle's own odometer is just the highest of these."""

    class Source(models.TextChoices):
        INITIAL = "initial", "Entered when the vehicle was added"
        MANUAL = "manual", "Manual entry"
        ASSIGNMENT = "assignment", "Hand-over / return"
        FUEL = "fuel", "Fuel fill"
        SERVICE = "service", "Service"

    company = models.ForeignKey("organization.Company", on_delete=models.PROTECT, related_name="+",
                                editable=False)
    vehicle = models.ForeignKey("vehicles.Vehicle", on_delete=models.PROTECT, related_name="odometer_readings")
    reading_on = models.DateField()
    odometer = models.PositiveIntegerField("Odometer (km)")
    source = models.CharField(max_length=12, choices=Source.choices, default=Source.MANUAL)
    note = models.CharField(max_length=200, blank=True)

    objects = CompanyQuerySet.as_manager()

    class Meta:
        ordering = ["-reading_on", "-id"]
        indexes = [models.Index(fields=["vehicle", "reading_on"])]

    def save(self, *args, **kwargs):
        self.company_id = self.vehicle.company_id
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.vehicle.plate_number} {self.odometer} km on {self.reading_on}"
