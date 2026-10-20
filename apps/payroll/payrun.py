"""The payroll run: the company's payroll policy, one run per company per month, a line for each person, and the
manual or imported additions and deductions. Imported at the bottom of models.py.

A line is rebuilt in place each time a draft run is calculated; once the run is approved it is frozen and never
touched by calculation again."""
import calendar
from decimal import Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from apps.audit.registry import audited
from apps.core.models import BaseModel
from apps.core.scoping import CompanyQuerySet

MONEY = dict(max_digits=12, decimal_places=3)
RATE = dict(max_digits=5, decimal_places=2, null=True, blank=True,
            validators=[MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("100"))])


@audited(module="payroll", company="company_id")
class PayrollPolicy(BaseModel):
    """How payroll is worked out for a company, from a date. Dated like pay rates: a new row ends the old one the day
    before, and a run keeps the numbers it was calculated with. Nothing is assumed: a company has no policy until
    someone sets one, and SIO rates left empty stop any Bahraini from being paid until they are filled in."""

    company = models.ForeignKey("organization.Company", on_delete=models.PROTECT, related_name="payroll_policies")
    effective_from = models.DateField()
    effective_to = models.DateField(null=True, blank=True)
    days_in_month = models.PositiveSmallIntegerField(
        "Days in a month", default=30, validators=[MinValueValidator(20), MaxValueValidator(31)],
        help_text="A day's pay for absence or unpaid leave is the monthly pay divided by this.")
    sio_employee_rate = models.DecimalField("SIO: employee rate for Bahrainis (%)", **RATE,
                                            help_text="Taken from the Bahraini employee's pay.")
    sio_employer_rate = models.DecimalField("SIO: employer rate for Bahrainis (%)", **RATE,
                                            help_text="Paid by the company on top. Shown as a cost, not on the payslip.")
    sio_employer_rate_other = models.DecimalField("SIO: employer rate for others (%)", **RATE,
                                                  help_text="Paid by the company for staff who are not Bahraini. "
                                                            "Leave empty if there is none.")
    sio_wage_floor = models.DecimalField("SIO: lowest wage counted (BHD)", max_digits=10, decimal_places=3,
                                         null=True, blank=True, validators=[MinValueValidator(Decimal("0"))])
    sio_wage_cap = models.DecimalField("SIO: highest wage counted (BHD)", max_digits=10, decimal_places=3,
                                       null=True, blank=True, validators=[MinValueValidator(Decimal("0"))])
    sio_on_paid_days = models.BooleanField(
        "SIO follows the days paid", default=False,
        help_text="Tick if unpaid days also reduce the SIO wage. Ask the accountant.")

    objects = CompanyQuerySet.as_manager()

    class Meta:
        ordering = ["company", "-effective_from"]
        constraints = [
            models.UniqueConstraint(fields=["company", "effective_from"], name="uniq_payroll_policy_start"),
            models.UniqueConstraint(fields=["company"], condition=models.Q(effective_to__isnull=True),
                                    name="one_open_payroll_policy"),
            models.CheckConstraint(
                condition=models.Q(effective_to__isnull=True) | models.Q(effective_to__gte=models.F("effective_from")),
                name="payroll_policy_ends_after_it_starts"),
        ]

    @property
    def sio_rates_set(self):
        return self.sio_employee_rate is not None and self.sio_employer_rate is not None

    def values(self):
        """What a run copies, so its numbers can be explained long after the policy changes."""
        def fix(v):
            return None if v is None else str(v)
        return {"days_in_month": self.days_in_month, "sio_employee_rate": fix(self.sio_employee_rate),
                "sio_employer_rate": fix(self.sio_employer_rate),
                "sio_employer_rate_other": fix(self.sio_employer_rate_other),
                "sio_wage_floor": fix(self.sio_wage_floor), "sio_wage_cap": fix(self.sio_wage_cap),
                "sio_on_paid_days": self.sio_on_paid_days}

    def __str__(self):
        return f"{self.company.code} payroll policy from {self.effective_from}"


@audited(module="payroll", company="company_id")
class PayrollRun(BaseModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SUBMITTED = "submitted", "Waiting for approval"
        APPROVED = "approved", "Approved"
        PAID = "paid", "Paid"
        CANCELLED = "cancelled", "Cancelled"

    class Kind(models.TextChoices):
        STAFF = "staff", "Staff"
        LABOR = "labor", "Direct labor"

    company = models.ForeignKey("organization.Company", on_delete=models.PROTECT, related_name="payroll_runs")
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.STAFF)
    year = models.PositiveSmallIntegerField()
    month = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(12)])
    period_start = models.DateField()
    period_end = models.DateField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    policy = models.ForeignKey(PayrollPolicy, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    policy_values = models.JSONField(default=dict, blank=True)
    prepared_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    calculated_at = models.DateTimeField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    note = models.CharField(max_length=500, blank=True)

    objects = CompanyQuerySet.as_manager()

    class Meta:
        ordering = ["-year", "-month", "company"]
        permissions = [("submit_payrollrun", "Can send payroll runs for approval"),
                       ("pay_payrollrun", "Can record payroll payments")]
        constraints = [models.UniqueConstraint(
            fields=["company", "kind", "year", "month"], condition=~models.Q(status="cancelled"),
            name="one_live_payroll_run_per_month",
            violation_error_message="This company already has a payroll run for that month.")]

    @property
    def label(self):
        return f"{calendar.month_name[self.month]} {self.year}"

    @property
    def frozen(self):
        return self.status in (self.Status.APPROVED, self.Status.PAID)

    def __str__(self):
        return f"{self.get_kind_display()} payroll {self.label}, {self.company.code}"


MONEY_FIELDS = ["gross", "absence_deduction", "sio_wage", "sio_employee", "sio_employer", "additions", "deductions", "net"]


@audited(module="payroll", company="company_id", subject="employee_id", mask=MONEY_FIELDS + ["iban", "breakdown"])
class PayrollLine(BaseModel):
    """One person's pay for the month, with the workings kept so it can always be explained."""
    run = models.ForeignKey(PayrollRun, on_delete=models.CASCADE, related_name="lines")
    employee = models.ForeignKey("employees.Employee", on_delete=models.PROTECT, related_name="payroll_lines")
    employee_no = models.CharField(max_length=30, blank=True)
    employee_name = models.CharField(max_length=170)
    nationality_kind = models.CharField(max_length=10)
    days_employed = models.PositiveSmallIntegerField(default=0)
    gross = models.DecimalField(**MONEY, default=Decimal("0"))
    unpaid_days = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0"))
    absence_deduction = models.DecimalField(**MONEY, default=Decimal("0"))
    sio_wage = models.DecimalField(**MONEY, default=Decimal("0"))
    sio_employee = models.DecimalField(**MONEY, default=Decimal("0"))
    sio_employer = models.DecimalField(**MONEY, default=Decimal("0"))
    additions = models.DecimalField(**MONEY, default=Decimal("0"))
    deductions = models.DecimalField(**MONEY, default=Decimal("0"))
    net = models.DecimalField(**MONEY, default=Decimal("0"))
    payment_method = models.CharField(max_length=4, blank=True)
    bank_name = models.CharField(max_length=80, blank=True)
    iban = models.CharField(max_length=34, blank=True)
    issues = models.JSONField(default=list, blank=True)
    breakdown = models.JSONField(default=dict, blank=True)
    paid_on = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["employee_no", "employee_name"]
        constraints = [models.UniqueConstraint(fields=["run", "employee"], name="uniq_payroll_line_per_person")]

    @property
    def company_id(self):
        return self.run.company_id

    @property
    def iban_masked(self):
        """The IBAN with the middle hidden, for a payslip: BH67 **** 3456."""
        return f"{self.iban[:4]} \u2022\u2022\u2022\u2022 {self.iban[-4:]}" if len(self.iban) > 8 else self.iban

    @property
    def total_earnings(self):
        return self.gross + self.additions

    @property
    def total_deductions(self):
        return self.absence_deduction + self.sio_employee + self.deductions

    def get_payment_label(self):
        return {"bank": "Bank transfer", "cash": "Cash"}.get(self.payment_method, "Not set")

    @property
    def blocking(self):
        return [i for i in self.issues if i.get("level") == "block"]

    @property
    def warnings(self):
        return [i for i in self.issues if i.get("level") == "warn"]

    def __str__(self):
        return f"{self.employee_name}, {self.run.label}"


@audited(module="payroll", company="company_id", subject="employee_id", mask=["amount"])
class PayrollAdjustment(BaseModel):
    """Something added to or taken from one person's pay in one run."""

    class Kind(models.TextChoices):
        ADDITION = "addition", "Addition"
        DEDUCTION = "deduction", "Deduction"

    class Category(models.TextChoices):
        BONUS = "bonus", "Bonus"
        INCENTIVE = "incentive", "Incentive"
        ADVANCE = "advance", "Salary advance"
        PENALTY = "penalty", "Penalty"
        RENEWAL = "renewal", "Renewal fee charged to the employee"
        FINE = "fine", "Vehicle fine"
        LOAN = "loan", "Loan instalment"
        OTHER = "other", "Other"

    run = models.ForeignKey(PayrollRun, on_delete=models.CASCADE, related_name="adjustments")
    employee = models.ForeignKey("employees.Employee", on_delete=models.PROTECT, related_name="payroll_adjustments")
    kind = models.CharField(max_length=10, choices=Kind.choices)
    category = models.CharField(max_length=10, choices=Category.choices)
    label = models.CharField(max_length=120)
    amount = models.DecimalField(**MONEY, validators=[MinValueValidator(Decimal("0.001"))])
    source = models.CharField(max_length=10, default="manual", help_text="manual, or the module it was picked up from")
    source_id = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        ordering = ["employee__employee_no", "id"]
        constraints = [models.UniqueConstraint(
            fields=["run", "source", "source_id"], condition=models.Q(source_id__isnull=False),
            name="uniq_payroll_adjustment_source_per_run")]

    @property
    def company_id(self):
        return self.run.company_id

    @property
    def signed(self):
        return self.amount if self.kind == self.Kind.ADDITION else -self.amount

    def __str__(self):
        return f"{self.label} ({self.get_kind_display()})"
