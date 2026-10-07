"""Scans and photos attached to fuel fills, service records, fines, accidents and loans. Imported at the bottom of models.py.

Files live in the same private storage as your compliance documents: they have no public address and every view
and download is permission-checked and written to the audit log."""
import uuid
from pathlib import Path

from django.db import models

from apps.audit.registry import audited
from apps.compliance.storage import private_storage
from apps.compliance.validators import validate_extension, validate_file_size
from apps.core.models import BaseModel
from apps.core.scoping import CompanyQuerySet

# the five things a file can belong to: model field -> the short name used in addresses and permissions
TARGET_KINDS = {"fuel_fill": "fuel", "service_record": "service", "fine": "fine", "accident": "accident", "loan": "loan"}


def vehicle_file_path(instance, filename):
    return (f"vehicles/{instance.company_id}/{instance.vehicle_id}/"
            f"{uuid.uuid4().hex}{Path(filename).suffix.lower()[:10]}")


def _belongs_only_to(field):
    return models.Q(**{f"{name}__isnull": name != field for name in TARGET_KINDS})


@audited(module="vehicles", company="company_id")
class VehicleFile(BaseModel):
    class Kind(models.TextChoices):
        RECEIPT = "receipt", "Receipt"
        INVOICE = "invoice", "Invoice"
        TICKET = "ticket", "Fine ticket"
        POLICE_REPORT = "police_report", "Police report"
        PHOTO = "photo", "Photo"
        CONTRACT = "contract", "Loan contract"
        STATEMENT = "statement", "Loan statement"
        OTHER = "other", "Other"

    company = models.ForeignKey("organization.Company", on_delete=models.PROTECT, related_name="+", editable=False)
    vehicle = models.ForeignKey("vehicles.Vehicle", on_delete=models.PROTECT, related_name="files")
    fuel_fill = models.ForeignKey("vehicles.FuelFill", null=True, blank=True, on_delete=models.PROTECT,
                                  related_name="files")
    service_record = models.ForeignKey("vehicles.ServiceRecord", null=True, blank=True, on_delete=models.PROTECT,
                                       related_name="files")
    fine = models.ForeignKey("vehicles.Fine", null=True, blank=True, on_delete=models.PROTECT, related_name="files")
    accident = models.ForeignKey("vehicles.Accident", null=True, blank=True, on_delete=models.PROTECT,
                                 related_name="files")
    loan = models.ForeignKey("vehicles.VehicleLoan", null=True, blank=True, on_delete=models.PROTECT,
                             related_name="files")
    kind = models.CharField(max_length=14, choices=Kind.choices, default=Kind.OTHER)
    title = models.CharField(max_length=120, blank=True)
    file = models.FileField(storage=private_storage, upload_to=vehicle_file_path, max_length=200,
                            validators=[validate_extension, validate_file_size])
    is_removed = models.BooleanField(default=False)
    removed_reason = models.CharField(max_length=200, blank=True)

    objects = CompanyQuerySet.as_manager()

    class Meta:
        ordering = ["-id"]
        constraints = [
            models.CheckConstraint(condition=models.Q(*[_belongs_only_to(n) for n in TARGET_KINDS], _connector="OR"),
                                   name="vehicle_file_has_exactly_one_owner"),
        ]

    def save(self, *args, **kwargs):
        self.company_id = self.vehicle.company_id
        super().save(*args, **kwargs)

    @property
    def target_kind(self):
        return next((short for name, short in TARGET_KINDS.items() if getattr(self, f"{name}_id")), None)

    @property
    def target(self):
        return next((getattr(self, name) for name in TARGET_KINDS if getattr(self, f"{name}_id")), None)

    @property
    def label(self):
        return self.title or self.get_kind_display()

    def describe(self):
        t, k = self.target, self.target_kind
        if k == "fuel":
            return f"Fuel, {t.litres} L on {t.filled_on:%d %b %Y}"
        if k == "service":
            return f"{t.get_kind_display()} on {t.serviced_on:%d %b %Y}"
        if k == "fine":
            return f"Fine {t.reference or t.offence}, {t.fined_on:%d %b %Y}"
        if k == "accident":
            return f"Accident on {t.occurred_on:%d %b %Y}"
        return f"Loan from {t.lender}"

    def __str__(self):
        return f"{self.vehicle.plate_number}: {self.label}"
