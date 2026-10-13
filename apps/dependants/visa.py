"""Family visa requests (new, renewal, cancellation) and what they cost. Imported at the bottom of models.py."""
from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models

from apps.audit.registry import audited
from apps.core.models import BaseModel
from apps.core.scoping import CompanyQuerySet

_MONEY = dict(max_digits=12, decimal_places=3, default=Decimal("0"), validators=[MinValueValidator(Decimal("0"))])


@audited(module="dependants", company="company_id", subject="employee_id")
class FamilyVisaRequest(BaseModel):
    """One request about one family member's visa. Approval is handled by the approval engine; this row follows
    the outcome and then tracks the processing by HR."""

    class Kind(models.TextChoices):
        NEW = "new", "New visa"
        RENEWAL = "renewal", "Renewal"
        CANCELLATION = "cancellation", "Cancellation"

    class Status(models.TextChoices):
        PENDING = "pending", "Waiting for approval"
        APPROVED = "approved", "Approved"
        SUBMITTED = "submitted", "Submitted to the authority"
        COMPLETED = "completed", "Completed"
        REJECTED = "rejected", "Rejected"
        CANCELLED = "cancelled", "Withdrawn"

    OPEN = (Status.PENDING, Status.APPROVED, Status.SUBMITTED)      # still in progress

    company = models.ForeignKey("organization.Company", on_delete=models.PROTECT, related_name="+")
    employee = models.ForeignKey("employees.Employee", on_delete=models.PROTECT, related_name="+",
                                 help_text="The sponsor.")
    dependant = models.ForeignKey("dependants.Dependant", on_delete=models.PROTECT, related_name="visa_requests")
    kind = models.CharField(max_length=12, choices=Kind.choices)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    reason = models.TextField(blank=True)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    submitted_on = models.DateField(null=True, blank=True)
    reference = models.CharField("Authority reference", max_length=60, blank=True)
    completed_on = models.DateField(null=True, blank=True)
    visa_document = models.ForeignKey("compliance.Document", null=True, blank=True, on_delete=models.SET_NULL,
                                      related_name="+", help_text="The visa this request produced.")

    objects = CompanyQuerySet.as_manager()

    class Meta:
        ordering = ["-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["dependant"], condition=models.Q(status__in=["pending", "approved", "submitted"]),
                name="uniq_open_visa_request_per_dependant",
                violation_error_message="This family member already has a visa request in progress."),
        ]
        indexes = [models.Index(fields=["company", "status"])]

    @property
    def title(self):
        d = self.dependant
        return f"{self.get_kind_display()}: {d.full_name} ({d.get_relationship_display().lower()} of {self.employee.full_name})"

    @property
    def detail(self):
        return f"Sponsor {self.employee.full_name} ({self.employee.employee_no})"

    def __str__(self):
        return self.title


@audited(module="dependants", company="company_id", subject="employee_id")
class VisaPayment(BaseModel):
    """A cost of a visa request. `charged_to_employee` is the same flag the compliance renewals use: payroll will
    deduct it once payroll exists."""

    company = models.ForeignKey("organization.Company", on_delete=models.PROTECT, related_name="+")
    employee = models.ForeignKey("employees.Employee", on_delete=models.PROTECT, related_name="+")
    request = models.ForeignKey(FamilyVisaRequest, on_delete=models.PROTECT, related_name="payments")
    description = models.CharField(max_length=120, blank=True)
    paid_on = models.DateField()
    government_fee = models.DecimalField(**_MONEY)
    service_fee = models.DecimalField(**_MONEY)
    fine = models.DecimalField(**_MONEY)
    reference = models.CharField(max_length=60, blank=True)
    charged_to_employee = models.BooleanField(default=False)
    remarks = models.TextField(blank=True)

    objects = CompanyQuerySet.as_manager()

    class Meta:
        ordering = ["-paid_on", "-id"]

    @property
    def total(self):
        return (self.government_fee or 0) + (self.service_fee or 0) + (self.fine or 0)

    def save(self, *args, **kwargs):
        self.employee_id = self.request.employee_id
        self.company_id = self.request.company_id
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.request} - {self.total} BHD"
