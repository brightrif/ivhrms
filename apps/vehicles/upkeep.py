"""Fuel fill-ups, service plans and service records. Imported at the bottom of models.py."""
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

from apps.audit.registry import audited
from apps.core.models import BaseModel
from apps.core.scoping import CompanyQuerySet


@audited(module="vehicles", company="company_id")
class FuelFill(BaseModel):
    """One fill-up. A wrong entry is cancelled (kept, with a reason), never edited or deleted."""
    company = models.ForeignKey("organization.Company", on_delete=models.PROTECT, related_name="+",
                                editable=False)
    vehicle = models.ForeignKey("vehicles.Vehicle", on_delete=models.PROTECT, related_name="fuel_fills")
    driver = models.ForeignKey("employees.Employee", null=True, blank=True, on_delete=models.PROTECT,
                               related_name="+", editable=False,
                               help_text="Whoever had the vehicle on that day, kept as it was then.")
    filled_on = models.DateField()
    litres = models.DecimalField(max_digits=8, decimal_places=2, validators=[MinValueValidator(0)])
    cost = models.DecimalField("Total cost (BHD)", max_digits=12, decimal_places=3,
                               validators=[MinValueValidator(0)])
    odometer = models.PositiveIntegerField("Odometer (km)")
    full_tank = models.BooleanField(default=True, help_text="Filled to the top. Needed to work out km per litre.")
    station = models.CharField(max_length=80, blank=True)
    notes = models.CharField(max_length=200, blank=True)
    reading = models.OneToOneField("vehicles.OdometerReading", null=True, blank=True, on_delete=models.PROTECT,
                                   related_name="+", editable=False)
    is_voided = models.BooleanField(default=False)
    voided_reason = models.CharField(max_length=200, blank=True)

    objects = CompanyQuerySet.as_manager()

    class Meta:
        ordering = ["-filled_on", "-id"]
        constraints = [models.CheckConstraint(condition=models.Q(litres__gt=0), name="fuel_litres_positive")]
        indexes = [models.Index(fields=["vehicle", "filled_on"])]

    def save(self, *args, **kwargs):
        self.company_id = self.vehicle.company_id
        super().save(*args, **kwargs)

    @property
    def price_per_litre(self):
        return self.cost / self.litres if self.litres else None

    def __str__(self):
        return f"{self.vehicle.plate_number} {self.litres} L on {self.filled_on}"


@audited(module="vehicles", company="company_id")
class ServicePlan(BaseModel):
    """A repeating job for one vehicle, e.g. an oil change every 5,000 km or 6 months (whichever comes first)."""
    company = models.ForeignKey("organization.Company", on_delete=models.PROTECT, related_name="+",
                                editable=False)
    vehicle = models.ForeignKey("vehicles.Vehicle", on_delete=models.PROTECT, related_name="service_plans")
    name = models.CharField(max_length=80)
    every_km = models.PositiveIntegerField("Repeat every (km)", null=True, blank=True,
                                           validators=[MinValueValidator(1)])
    every_months = models.PositiveSmallIntegerField("Repeat every (months)", null=True, blank=True,
                                                    validators=[MinValueValidator(1)])
    warn_km = models.PositiveIntegerField("Warn when this many km are left", default=500)
    warn_days = models.PositiveSmallIntegerField("Warn when this many days are left", default=30)
    # Where counting starts, until the first service record for this plan is logged.
    baseline_on = models.DateField("Last done on")
    baseline_km = models.PositiveIntegerField("Last done at (km)")
    is_active = models.BooleanField(default=True)

    objects = CompanyQuerySet.as_manager()

    class Meta:
        ordering = ["name"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(every_km__isnull=False) | models.Q(every_months__isnull=False),
                name="service_plan_has_an_interval"),
            models.UniqueConstraint(fields=["vehicle", "name"], condition=models.Q(is_active=True),
                                    name="uniq_active_service_plan_name",
                                    violation_error_message="This vehicle already has an active plan with this name."),
        ]

    def save(self, *args, **kwargs):
        self.company_id = self.vehicle.company_id
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} ({self.vehicle.plate_number})"


@audited(module="vehicles", company="company_id")
class ServiceRecord(BaseModel):
    """Work done on a vehicle. Linking it to plans is what resets their counters."""

    class Kind(models.TextChoices):
        SCHEDULED = "scheduled", "Scheduled service"
        REPAIR = "repair", "Repair"
        OTHER = "other", "Other work"

    company = models.ForeignKey("organization.Company", on_delete=models.PROTECT, related_name="+",
                                editable=False)
    vehicle = models.ForeignKey("vehicles.Vehicle", on_delete=models.PROTECT, related_name="service_records")
    plans = models.ManyToManyField(ServicePlan, blank=True, related_name="records")
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.SCHEDULED)
    serviced_on = models.DateField()
    odometer = models.PositiveIntegerField("Odometer (km)")
    garage = models.CharField(max_length=80, blank=True)
    description = models.TextField(blank=True)
    parts_cost = models.DecimalField("Parts (BHD)", max_digits=12, decimal_places=3, default=0,
                                     validators=[MinValueValidator(0)])
    labour_cost = models.DecimalField("Labour (BHD)", max_digits=12, decimal_places=3, default=0,
                                      validators=[MinValueValidator(0)])
    invoice_no = models.CharField(max_length=40, blank=True)
    reading = models.OneToOneField("vehicles.OdometerReading", null=True, blank=True, on_delete=models.PROTECT,
                                   related_name="+", editable=False)
    is_voided = models.BooleanField(default=False)
    voided_reason = models.CharField(max_length=200, blank=True)

    objects = CompanyQuerySet.as_manager()

    class Meta:
        ordering = ["-serviced_on", "-id"]
        indexes = [models.Index(fields=["vehicle", "serviced_on"])]

    def save(self, *args, **kwargs):
        self.company_id = self.vehicle.company_id
        super().save(*args, **kwargs)

    @property
    def total_cost(self):
        return (self.parts_cost or 0) + (self.labour_cost or 0)

    def __str__(self):
        return f"{self.vehicle.plate_number} {self.get_kind_display()} on {self.serviced_on}"


class PlanAlertLog(models.Model):
    """Which service alert went out for which plan. The unique key is what stops duplicate emails."""
    plan = models.ForeignKey(ServicePlan, on_delete=models.CASCADE, related_name="alerts")
    state = models.CharField(max_length=8)                    # "due" (coming up) or "expired" (overdue)
    anchor = models.PositiveIntegerField(default=0)           # id of the latest service record: a new cycle after each service
    sent_at = models.DateTimeField(default=timezone.now)
    recipients = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["-sent_at"]
        constraints = [models.UniqueConstraint(fields=["plan", "state", "anchor"], name="uniq_alert_per_plan_cycle")]
