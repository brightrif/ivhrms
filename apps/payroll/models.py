"""Payroll: what each person is paid, and how it reaches them.

Phase 1 holds the staff salary structure (basic salary plus allowances, effective-dated) and each person's payment
details (bank or cash). Labor workers keep their pay terms under Labor; contractor-supplied labor is paid through the
contractor statement and has no place here."""
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models.functions import Lower

from apps.audit.registry import audited
from apps.core.models import BaseModel
from apps.core.scoping import CompanyQuerySet

MONEY = dict(max_digits=10, decimal_places=3, validators=[MinValueValidator(Decimal("0.001"))])


@audited(module="payroll", company="company_id")
class AllowanceType(BaseModel):
    """A kind of allowance the company pays on top of basic salary: housing, transport and so on."""
    company = models.ForeignKey("organization.Company", on_delete=models.PROTECT, related_name="allowance_types")
    name = models.CharField(max_length=80)
    counts_for_sio = models.BooleanField(
        "Counts towards the SIO wage", default=False,
        help_text="Tick if the SIO contribution is worked out on this allowance too. Ask the accountant; "
                  "it only matters for Bahraini staff.")
    is_active = models.BooleanField(default=True)

    counts_for_gratuity = models.BooleanField(
        "Social allowance (counts for gratuity)", default=False,
        help_text="Tick for the social allowance, if the company pays one. Gratuity is worked out on basic salary plus "
                  "this allowance only.")

    objects = CompanyQuerySet.as_manager()

    class Meta:
        ordering = ["company", "name"]
        constraints = [models.UniqueConstraint(
            Lower("name"), "company", name="uniq_allowance_type_name_per_company",
            violation_error_message="This company already has an allowance with this name.")]

    def __str__(self):
        return self.name


@audited(module="payroll", company="company_id", subject="employee_id", mask=["basic"])
class SalaryRecord(BaseModel):
    """A staff member's salary from a date. A new record ends the old one the day before, so a past month can always
    be priced as it was. Amounts are hidden in the audit log."""

    class Reason(models.TextChoices):
        JOINING = "joining", "Joining salary"
        INCREASE = "increase", "Increase"
        PROMOTION = "promotion", "Promotion"
        OTHER = "other", "Other"

    company = models.ForeignKey("organization.Company", on_delete=models.PROTECT, related_name="+", editable=False)
    employee = models.ForeignKey("employees.Employee", on_delete=models.CASCADE, related_name="salary_records")
    effective_from = models.DateField()
    effective_to = models.DateField(null=True, blank=True)
    basic = models.DecimalField("Basic salary (BHD)", **MONEY)
    reason = models.CharField(max_length=12, choices=Reason.choices)
    remarks = models.CharField(max_length=255, blank=True)

    objects = CompanyQuerySet.as_manager()

    class Meta:
        ordering = ["-effective_from"]
        constraints = [
            models.UniqueConstraint(fields=["employee", "effective_from"], name="uniq_salary_record_start"),
            models.UniqueConstraint(fields=["employee"], condition=models.Q(effective_to__isnull=True),
                                    name="one_open_salary_record"),
            models.CheckConstraint(
                condition=models.Q(effective_to__isnull=True) | models.Q(effective_to__gte=models.F("effective_from")),
                name="salary_record_ends_after_it_starts"),
        ]

    def save(self, *args, **kwargs):
        self.company_id = self.employee.company_id
        super().save(*args, **kwargs)

    @property
    def allowance_total(self):
        return sum((a.amount for a in self.allowances.all()), Decimal("0"))

    @property
    def total(self):
        """Basic plus every allowance: what the person is paid for a full month."""
        return self.basic + self.allowance_total

    @property
    def sio_wage(self):
        """Basic plus the allowances marked as counting towards SIO. Phase 2 uses it for Bahraini staff."""
        return self.basic + sum((a.amount for a in self.allowances.all() if a.allowance_type.counts_for_sio),
                                Decimal("0"))

    @property
    def gratuity_wage(self):
        """Basic plus the allowances marked as the social allowance: the wage end-of-service gratuity is worked on."""
        return self.basic + sum((a.amount for a in self.allowances.all() if a.allowance_type.counts_for_gratuity),
                                Decimal("0"))

    def __str__(self):
        return f"{self.employee} from {self.effective_from}"


@audited(module="payroll", company="company_id", subject="employee_id", mask=["amount"])
class SalaryAllowance(BaseModel):
    """One allowance on a salary record."""
    record = models.ForeignKey(SalaryRecord, on_delete=models.CASCADE, related_name="allowances")
    allowance_type = models.ForeignKey(AllowanceType, on_delete=models.PROTECT, related_name="+")
    amount = models.DecimalField("Amount (BHD)", **MONEY)

    class Meta:
        ordering = ["allowance_type__name"]
        constraints = [models.UniqueConstraint(fields=["record", "allowance_type"], name="uniq_allowance_per_record")]

    @property
    def employee_id(self):
        return self.record.employee_id

    @property
    def company_id(self):
        return self.record.company_id

    def __str__(self):
        return f"{self.allowance_type} on {self.record}"


@audited(module="payroll", company="company_id", subject="employee_id", mask=["iban"])
class PaymentDetail(BaseModel):
    """How a person is paid: into a bank account, or in cash. Staff and direct labor."""

    class Method(models.TextChoices):
        BANK = "bank", "Bank transfer"
        CASH = "cash", "Cash"

    company = models.ForeignKey("organization.Company", on_delete=models.PROTECT, related_name="+", editable=False)
    employee = models.OneToOneField("employees.Employee", on_delete=models.CASCADE, related_name="payment_detail")
    method = models.CharField(max_length=4, choices=Method.choices)
    bank_name = models.CharField(max_length=80, blank=True)
    iban = models.CharField("IBAN", max_length=34, blank=True)

    objects = CompanyQuerySet.as_manager()

    def clean(self):
        super().clean()
        if self.method == self.Method.BANK and not (self.bank_name and self.iban):
            raise ValidationError("A bank transfer needs the bank name and the IBAN.")

    def save(self, *args, **kwargs):
        self.company_id = self.employee.company_id
        super().save(*args, **kwargs)

    @property
    def iban_spaced(self):
        return " ".join(self.iban[i:i + 4] for i in range(0, len(self.iban), 4))

    def __str__(self):
        return f"{self.employee}: {self.get_method_display()}"


from . import payrun  # noqa: E402,F401  (registers the payroll run models)
from . import finalsettlement  # noqa: E402,F401  (registers the final settlement models)

from . import loans  # noqa: E402,F401  (loans and salary advances)
