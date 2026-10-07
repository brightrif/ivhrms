"""Traffic fines and accidents. Imported at the bottom of models.py."""
from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models

from apps.audit.registry import audited
from apps.core.models import BaseModel
from apps.core.scoping import CompanyQuerySet


@audited(module="vehicles", company="company_id", subject="driver_id")
class Fine(BaseModel):
    """A traffic fine. It is tied to whoever had the vehicle that day, because fines arrive weeks later."""

    class Status(models.TextChoices):
        UNPAID = "unpaid", "Unpaid"
        PAID = "paid", "Paid"

    company = models.ForeignKey("organization.Company", on_delete=models.PROTECT, related_name="+",
                                editable=False)
    vehicle = models.ForeignKey("vehicles.Vehicle", on_delete=models.PROTECT, related_name="fines")
    driver = models.ForeignKey("employees.Employee", null=True, blank=True, on_delete=models.PROTECT,
                               related_name="vehicle_fines")
    fined_on = models.DateField("Date of the offence")
    reference = models.CharField("Fine / ticket number", max_length=40, blank=True)
    offence = models.CharField(max_length=200)
    location = models.CharField(max_length=120, blank=True)
    amount = models.DecimalField("Amount (BHD)", max_digits=12, decimal_places=3, validators=[MinValueValidator(0)])
    status = models.CharField(max_length=6, choices=Status.choices, default=Status.UNPAID)
    paid_on = models.DateField(null=True, blank=True)
    paid_amount = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True,
                                      validators=[MinValueValidator(0)])
    payment_reference = models.CharField(max_length=60, blank=True)
    charged_to_employee = models.BooleanField(
        default=False, help_text="The driver bears this cost (payroll will deduct it once payroll exists).")
    notes = models.TextField(blank=True)
    is_voided = models.BooleanField(default=False)
    voided_reason = models.CharField(max_length=200, blank=True)

    objects = CompanyQuerySet.as_manager()

    class Meta:
        ordering = ["-fined_on", "-id"]
        permissions = [("pay_fine", "Can record fine payments")]
        constraints = [
            models.CheckConstraint(condition=models.Q(amount__gt=0), name="fine_amount_positive"),
            models.CheckConstraint(
                condition=models.Q(status="unpaid") | models.Q(paid_on__isnull=False), name="fine_paid_has_a_date"),
            models.UniqueConstraint(
                fields=["vehicle", "reference"], name="uniq_fine_reference_per_vehicle",
                condition=~models.Q(reference="") & models.Q(is_voided=False),
                violation_error_message="This fine is already recorded for this vehicle."),
        ]
        indexes = [models.Index(fields=["vehicle", "fined_on"]), models.Index(fields=["status", "is_voided"])]

    def save(self, *args, **kwargs):
        self.company_id = self.vehicle.company_id
        super().save(*args, **kwargs)

    @property
    def display_amount(self):
        """What was paid once it is paid, otherwise what is owed."""
        return self.paid_amount if self.status == self.Status.PAID and self.paid_amount is not None else self.amount

    def __str__(self):
        return f"{self.vehicle.plate_number} fine {self.reference or self.pk} on {self.fined_on}"


@audited(module="vehicles", company="company_id", subject="driver_id")
class Accident(BaseModel):
    """An accident, from the report to the insurance outcome. Repairs are logged in Maintenance and linked here."""

    class Fault(models.TextChoices):
        UNKNOWN = "unknown", "Not decided"
        DRIVER = "driver", "Our driver"
        OTHER = "other", "Other party"
        SHARED = "shared", "Shared"

    class Claim(models.TextChoices):
        NONE = "none", "No claim"
        FILED = "filed", "Claim filed"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        PAID = "paid", "Paid out"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        CLOSED = "closed", "Closed"

    company = models.ForeignKey("organization.Company", on_delete=models.PROTECT, related_name="+",
                                editable=False)
    vehicle = models.ForeignKey("vehicles.Vehicle", on_delete=models.PROTECT, related_name="accidents")
    driver = models.ForeignKey("employees.Employee", null=True, blank=True, on_delete=models.PROTECT,
                               related_name="vehicle_accidents")
    occurred_on = models.DateField("Date of the accident")
    location = models.CharField(max_length=120, blank=True)
    description = models.TextField(help_text="What happened, and what was damaged.")
    police_report_no = models.CharField("Police report number", max_length=40, blank=True)
    fault = models.CharField(max_length=8, choices=Fault.choices, default=Fault.UNKNOWN)
    injuries = models.BooleanField(default=False, help_text="Was anyone hurt?")
    third_party = models.BooleanField("Another vehicle or person involved", default=False)
    third_party_details = models.TextField(blank=True, help_text="Name, plate, phone and insurer of the other party.")
    claim_status = models.CharField("Insurance claim", max_length=8, choices=Claim.choices, default=Claim.NONE)
    claim_no = models.CharField("Claim number", max_length=40, blank=True)
    insurance_recovered = models.DecimalField("Recovered from insurance (BHD)", max_digits=12, decimal_places=3,
                                              default=Decimal("0"), validators=[MinValueValidator(0)])
    repairs = models.ManyToManyField("vehicles.ServiceRecord", blank=True, related_name="accidents")
    status = models.CharField(max_length=6, choices=Status.choices, default=Status.OPEN)
    closed_on = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)
    is_voided = models.BooleanField(default=False)
    voided_reason = models.CharField(max_length=200, blank=True)

    objects = CompanyQuerySet.as_manager()

    class Meta:
        ordering = ["-occurred_on", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status="open") | models.Q(closed_on__isnull=False),
                name="accident_closed_has_a_date"),
        ]
        indexes = [models.Index(fields=["vehicle", "occurred_on"])]

    def save(self, *args, **kwargs):
        self.company_id = self.vehicle.company_id
        super().save(*args, **kwargs)

    @property
    def live_repairs(self):
        """Linked repairs that were not cancelled. Use prefetch_related("repairs") to avoid a query each."""
        return [r for r in self.repairs.all() if not r.is_voided]

    @property
    def repair_cost(self):
        return sum((r.total_cost for r in self.live_repairs), Decimal("0"))      # summed in Python: BHD has 3 decimals

    @property
    def net_cost(self):
        return self.repair_cost - (self.insurance_recovered or Decimal("0"))

    def __str__(self):
        return f"{self.vehicle.plate_number} accident on {self.occurred_on}"
