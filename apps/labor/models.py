"""Labor management: who the site workers are, who supplies them, what trade they work in and what they are paid.

A labor worker is still an Employee (worker_type = "labor"), so attendance, leave, documents and payroll treat
them like everyone else. These models hold only what is specific to labor."""
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models.functions import Lower

from apps.audit.registry import audited
from apps.core.models import BaseModel
from apps.core.scoping import CompanyQuerySet


@audited(module="labor")
class Trade(BaseModel):
    """A skill or trade: mason, electrician, helper... Shared by every company."""
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=80)
    name_ar = models.CharField("Name (Arabic)", max_length=80, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(Lower("name"), name="uniq_trade_name",
                                               violation_error_message="There is already a trade with this name.")]

    def __str__(self):
        return self.name


@audited(module="labor", company="company_id")
class Contractor(BaseModel):
    """A company that supplies workers and invoices for them."""
    company = models.ForeignKey("organization.Company", on_delete=models.PROTECT, related_name="contractors")
    name = models.CharField(max_length=150)
    name_ar = models.CharField("Name (Arabic)", max_length=150, blank=True)
    cr_number = models.CharField("Commercial registration no.", max_length=40, blank=True)
    contact_person = models.CharField(max_length=100, blank=True)
    phone = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    address = models.TextField(blank=True)
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    objects = CompanyQuerySet.as_manager()

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(
            Lower("name"), "company", name="uniq_contractor_name_per_company",
            violation_error_message="This company already has a contractor with this name.")]

    def __str__(self):
        return self.name


@audited(module="labor", company="company_id", subject="employee_id")
class LaborProfile(BaseModel):
    """What makes an Employee a site worker: how they are engaged, who supplies them, and their trade."""

    class Engagement(models.TextChoices):
        DIRECT = "direct", "Direct employee"
        CONTRACTED = "contracted", "Contracted (daily)"

    company = models.ForeignKey("organization.Company", on_delete=models.PROTECT, related_name="+", editable=False)
    employee = models.OneToOneField("employees.Employee", on_delete=models.PROTECT, related_name="labor_profile")
    engagement = models.CharField(max_length=12, choices=Engagement.choices, default=Engagement.DIRECT)
    contractor = models.ForeignKey(Contractor, null=True, blank=True, on_delete=models.PROTECT, related_name="workers",
                                   help_text="Only if a contractor company supplies this worker.")
    trade = models.ForeignKey(Trade, on_delete=models.PROTECT, related_name="workers")
    notes = models.TextField(blank=True)
    serves_all_projects = models.BooleanField(
        default=False, help_text="Drivers, storekeepers and others who work for every project. "
                                 "They appear on every active project's team.")

    objects = CompanyQuerySet.as_manager()

    class Meta:
        ordering = ["employee__employee_no"]
        constraints = [models.CheckConstraint(
            condition=models.Q(engagement="contracted") | models.Q(contractor__isnull=True),
            name="direct_labor_has_no_contractor")]
        indexes = [models.Index(fields=["company", "engagement"])]

    def save(self, *args, **kwargs):
        self.company_id = self.employee.company_id
        super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        if self.engagement == self.Engagement.DIRECT and self.contractor_id:
            raise ValidationError({"contractor": "A direct employee has no contractor."})
        if self.contractor_id and self.employee_id and self.contractor.company_id != self.employee.company_id:
            raise ValidationError({"contractor": "The contractor belongs to another company."})

    @property
    def current_rate(self):
        """The open rate row (no end date). Reads self.rates.all(), so prefetch_related("rates") makes it free."""
        return next((r for r in self.rates.all() if r.effective_to is None), None)

    def __str__(self):
        return f"{self.employee.employee_no} {self.employee.full_name} ({self.trade})"


@audited(module="labor", company="company_id", subject="employee_id")
class LaborRate(BaseModel):
    """Effective-dated pay terms. A new row closes the old one, so a past month can always be priced as it was."""

    class WageBasis(models.TextChoices):
        DAILY = "daily", "Daily wage"
        MONTHLY = "monthly", "Monthly salary"

    company = models.ForeignKey("organization.Company", on_delete=models.PROTECT, related_name="+", editable=False)
    profile = models.ForeignKey(LaborProfile, on_delete=models.CASCADE, related_name="rates")
    wage_basis = models.CharField(max_length=8, choices=WageBasis.choices, default=WageBasis.DAILY)
    rate = models.DecimalField("Rate (BHD)", max_digits=10, decimal_places=3,
                               validators=[MinValueValidator(Decimal("0.001"))],
                               help_text="Per day for a daily wage, per month for a monthly salary.")
    standard_hours = models.DecimalField("Standard hours per day", max_digits=4, decimal_places=2, default=Decimal("8"),
                                         validators=[MinValueValidator(Decimal("1")), MaxValueValidator(Decimal("24"))],
                                         help_text="Hours beyond this count as overtime.")
    overtime_eligible = models.BooleanField(default=True)
    effective_from = models.DateField()
    effective_to = models.DateField(null=True, blank=True)

    objects = CompanyQuerySet.as_manager()

    class Meta:
        ordering = ["-effective_from"]
        constraints = [
            models.UniqueConstraint(fields=["profile", "effective_from"], name="uniq_labor_rate_start"),
            models.UniqueConstraint(fields=["profile"], condition=models.Q(effective_to__isnull=True),
                                    name="one_open_labor_rate"),
            models.CheckConstraint(
                condition=models.Q(effective_to__isnull=True) | models.Q(effective_to__gte=models.F("effective_from")),
                name="labor_rate_ends_after_it_starts"),
        ]

    def save(self, *args, **kwargs):
        self.company_id = self.profile.company_id
        super().save(*args, **kwargs)

    @property
    def employee_id(self):
        """The worker this row is about. The audit log uses it for 'everything about this person'."""
        return self.profile.employee_id

    @property
    def unit(self):
        return "day" if self.wage_basis == self.WageBasis.DAILY else "month"

    @property
    def label(self):
        return f"{self.rate:.3f} BHD / {self.unit}"

    def __str__(self):
        return f"{self.profile.employee.employee_no} {self.label} from {self.effective_from}"


from .allocation import LaborAllocation, WorkOrder  # noqa: E402,F401  (registers the two models)
from .timesheet import TimeEntry  # noqa: E402,F401  (registers the hours model)
from .overtime import OvertimeClaim, OvertimePolicy  # noqa: E402,F401  (registers the overtime models)
