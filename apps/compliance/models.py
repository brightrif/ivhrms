from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

from apps.audit.registry import audited
from apps.core.models import BaseModel
from apps.core.scoping import CompanyQuerySet

from . import schedule
from .storage import document_path, private_storage, receipt_path
from .validators import validate_extension, validate_file_size


@audited(module="compliance")
class DocumentType(BaseModel):
    """A kind of certificate or licence the firm must keep valid. Editable data, not code."""

    class AppliesTo(models.TextChoices):
        COMPANY = "company", "Company"
        EMPLOYEE = "employee", "Employee"        # reserved for the employee documents phase
        DEPENDANT = "dependant", "Dependant"

    code = models.SlugField(max_length=40, unique=True)
    name = models.CharField(max_length=120)
    name_ar = models.CharField("Name (Arabic)", max_length=120, blank=True)
    applies_to = models.CharField(max_length=10, choices=AppliesTo.choices, default=AppliesTo.COMPANY)
    authority = models.CharField("Issuing authority", max_length=150, blank=True)
    default_validity_months = models.PositiveSmallIntegerField(
        null=True, blank=True, help_text="Used to suggest the next expiry date when renewing.")
    alert_days = models.JSONField(
        default=schedule.default_alert_days,
        help_text="Days before expiry when alerts are sent. 0 is the expiry day.")
    overdue_repeat_days = models.PositiveSmallIntegerField(
        default=7, validators=[MinValueValidator(1)],
        help_text="After expiry, the alert repeats every this many days until it is renewed.")
    is_mandatory = models.BooleanField(
        default=False, help_text="Every company must hold one. Shown as missing on the dashboard if not.")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def clean(self):
        try:
            self.alert_days = schedule.normalise_alert_days(self.alert_days)
        except ValueError as exc:
            raise ValidationError({"alert_days": str(exc)})

    def __str__(self):
        return self.name


@audited(module="compliance", company="company_id")
class Document(BaseModel):
    """One issued certificate. A renewal creates a NEW row linked to the old one, so history is kept."""
    company = models.ForeignKey("organization.Company", on_delete=models.PROTECT,
                                related_name="compliance_documents")
    document_type = models.ForeignKey(DocumentType, on_delete=models.PROTECT, related_name="documents")
    reference_name = models.CharField(max_length=120, blank=True)
    number = models.CharField(max_length=60, blank=True)
    issue_date = models.DateField(null=True, blank=True)
    expiry_date = models.DateField()
    responsible = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="+")
    agent_name = models.CharField(max_length=120, blank=True)
    file = models.FileField(storage=private_storage, upload_to=document_path, max_length=200, blank=True,
                            validators=[validate_extension, validate_file_size])
    notes = models.TextField(blank=True)
    previous = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT,
                                 related_name="next_versions")
    is_current = models.BooleanField(default=True)

    objects = CompanyQuerySet.as_manager()

    class Meta:
        ordering = ["expiry_date", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "document_type", "reference_name"],
                condition=models.Q(is_current=True), name="uniq_current_compliance_document",
                violation_error_message="This company already has a current document of this type with "
                                        "the same reference. Renew that one, or give this one a "
                                        "different reference name."),
            models.CheckConstraint(
                condition=models.Q(issue_date__isnull=True) | models.Q(expiry_date__gte=models.F("issue_date")),
                name="compliance_expiry_after_issue"),
        ]
        indexes = [models.Index(fields=["company", "is_current", "expiry_date"])]

    @property
    def label(self):
        name = self.document_type.name
        return f"{name} - {self.reference_name}" if self.reference_name else name

    @property
    def days_left(self):
        return (self.expiry_date - timezone.localdate()).days

    @property
    def state(self):
        return schedule.state_for(self.days_left, self.document_type.alert_days)

    @property
    def due_text(self):
        n = self.days_left
        if n > 1:
            return f"in {n} days"
        return {1: "tomorrow", 0: "today", -1: "1 day ago"}.get(n, f"{-n} days ago")

    def older_versions(self):
        chain, node, seen = [], self.previous, set()
        while node is not None and node.pk not in seen:
            chain.append(node)
            seen.add(node.pk)
            node = node.previous
        return chain

    def newer_version(self):
        return self.next_versions.order_by("-id").first()

    def __str__(self):
        return f"{self.label} ({self.company.code})"


@audited(module="compliance", company="company_id")
class RenewalTask(BaseModel):
    """The to-do that opens before a document expires."""

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    company = models.ForeignKey("organization.Company", on_delete=models.PROTECT, related_name="+",
                                editable=False)
    document = models.ForeignKey(Document, on_delete=models.PROTECT, related_name="renewal_tasks")
    assignee = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.SET_NULL,
                                 related_name="+")
    due_date = models.DateField()
    estimated_cost = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True,
                                         validators=[MinValueValidator(0)])
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN)
    notes = models.TextField(blank=True)
    new_document = models.ForeignKey(Document, null=True, blank=True, on_delete=models.SET_NULL,
                                     related_name="created_by_task")
    completed_at = models.DateTimeField(null=True, blank=True)

    objects = CompanyQuerySet.as_manager()

    class Meta:
        ordering = ["due_date", "id"]
        constraints = [
            models.UniqueConstraint(fields=["document"], condition=models.Q(status="open"),
                                    name="one_open_renewal_per_document",
                                    violation_error_message="A renewal is already open for this document."),
        ]

    def save(self, *args, **kwargs):
        self.company_id = self.document.company_id
        super().save(*args, **kwargs)

    @property
    def paid_total(self):
        return sum((p.total for p in self.payments.all()), Decimal("0"))     # sum in Python, not SQL

    def __str__(self):
        return f"Renewal of {self.document}"


@audited(module="compliance", company="company_id")
class RenewalPayment(BaseModel):
    """Money actually paid for a renewal. A renewal can have several (CR fee, Chamber fee, agent...)."""

    class Method(models.TextChoices):
        BANK_TRANSFER = "bank_transfer", "Bank transfer"
        CARD = "card", "Card / online"
        CASH = "cash", "Cash"
        CHEQUE = "cheque", "Cheque"
        OTHER = "other", "Other"

    company = models.ForeignKey("organization.Company", on_delete=models.PROTECT, related_name="+",
                                editable=False)
    task = models.ForeignKey(RenewalTask, on_delete=models.PROTECT, related_name="payments")
    description = models.CharField(max_length=120, blank=True, help_text='e.g. "CR renewal fee"')
    paid_on = models.DateField()
    government_fee = models.DecimalField(max_digits=12, decimal_places=3, default=Decimal("0"),
                                         validators=[MinValueValidator(0)])
    service_fee = models.DecimalField(max_digits=12, decimal_places=3, default=Decimal("0"),
                                      validators=[MinValueValidator(0)])
    fine = models.DecimalField(max_digits=12, decimal_places=3, default=Decimal("0"),
                               validators=[MinValueValidator(0)])
    method = models.CharField(max_length=15, choices=Method.choices, default=Method.BANK_TRANSFER)
    reference = models.CharField(max_length=80, blank=True)
    receipt = models.FileField(storage=private_storage, upload_to=receipt_path, max_length=200, blank=True,
                               validators=[validate_extension, validate_file_size])
    paid_by = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.SET_NULL,
                                related_name="+")
    remarks = models.TextField(blank=True)

    objects = CompanyQuerySet.as_manager()

    class Meta:
        ordering = ["-paid_on", "-id"]

    @property
    def total(self):
        return (self.government_fee or Decimal("0")) + (self.service_fee or Decimal("0")) + (self.fine or Decimal("0"))

    def save(self, *args, **kwargs):
        self.company_id = self.task.company_id
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.paid_on} {self.total}"


class AlertLog(models.Model):
    """Which alert was sent for which document. The unique key is what stops duplicate alerts."""
    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="alerts")
    bucket = models.SmallIntegerField()
    sent_at = models.DateTimeField(default=timezone.now)
    recipients = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["-sent_at"]
        constraints = [models.UniqueConstraint(fields=["document", "bucket"], name="uniq_alert_per_bucket")]
