"""A final settlement: what a leaver is owed or owes, worked out under Bahrain law, approved by Management and paid.
Imported at the bottom of models.py. System lines (wages, leave, notice, gratuity) are rebuilt each time the case is
calculated; lines HR adds, and charges picked up from Compliance and Vehicles, are kept."""
from decimal import Decimal

from django.conf import settings
from django.db import models

from apps.audit.registry import audited
from apps.core.models import BaseModel
from apps.core.scoping import CompanyQuerySet

MONEY = dict(max_digits=12, decimal_places=3)


@audited(module="payroll", company="company_id", subject="employee_id", mask=["total", "iban", "details", "issues"])
class FinalSettlement(BaseModel):
    class Reason(models.TextChoices):
        RESIGNATION = "resignation", "Resignation"
        TERMINATION = "termination", "Ended by the employer, with notice"
        END_OF_CONTRACT = "end_of_contract", "End of a fixed-term contract"
        MUTUAL = "mutual", "Mutual agreement"
        SUMMARY = "summary", "Dismissal without notice (Article 107)"
        DEATH = "death", "Death or incapacity"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SUBMITTED = "submitted", "Waiting for approval"
        APPROVED = "approved", "Approved"
        PAID = "paid", "Paid"
        CANCELLED = "cancelled", "Cancelled"

    class NoticeTreatment(models.TextChoices):
        AUTO = "auto", "Automatic"
        NONE = "none", "Nothing"
        PAY = "pay", "Employer pays in lieu of notice"
        RECOVER = "recover", "Recover from the employee"

    company = models.ForeignKey("organization.Company", on_delete=models.PROTECT, related_name="+", editable=False)
    employee = models.ForeignKey("employees.Employee", on_delete=models.PROTECT, related_name="settlements")
    reason = models.CharField(max_length=16, choices=Reason.choices)
    notice_date = models.DateField("Date notice was given", null=True, blank=True)
    last_working_day = models.DateField()
    notice_required_days = models.PositiveSmallIntegerField(
        "Notice required (days)", default=30, help_text="30 is the legal minimum; the contract may say more.")
    notice_treatment = models.CharField(max_length=8, choices=NoticeTreatment.choices, default=NoticeTreatment.AUTO)
    leave_type = models.ForeignKey("leave.LeaveType", null=True, blank=True, on_delete=models.PROTECT, related_name="+",
                                   verbose_name="Annual leave type")
    gratuity_decision = models.BooleanField(
        "Gratuity payable", null=True, blank=True,
        help_text="Leave empty to follow the rule for the reason. It must be decided for a dismissal without notice and "
                  "for a GCC national.")
    gratuity_note = models.CharField("Why", max_length=255, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    total = models.DecimalField("Net amount", default=Decimal("0"), **MONEY)
    issues = models.JSONField(default=list, blank=True)
    details = models.JSONField(default=dict, blank=True)
    payment_method = models.CharField(max_length=4, blank=True)
    bank_name = models.CharField(max_length=80, blank=True)
    iban = models.CharField(max_length=34, blank=True)
    paid_on = models.DateField(null=True, blank=True)
    prepared_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="+")
    calculated_at = models.DateTimeField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    note = models.CharField(max_length=500, blank=True)

    objects = CompanyQuerySet.as_manager()

    class Meta:
        ordering = ["-last_working_day", "-id"]
        permissions = [("submit_finalsettlement", "Can send final settlements for approval"),
                       ("pay_finalsettlement", "Can record final settlement payments")]
        constraints = [models.UniqueConstraint(
            fields=["employee"], condition=~models.Q(status="cancelled"), name="one_live_settlement_per_employee",
            violation_error_message="This person already has a final settlement.")]

    def save(self, *args, **kwargs):
        self.company_id = self.employee.company_id
        super().save(*args, **kwargs)

    @property
    def frozen(self):
        return self.status in (self.Status.APPROVED, self.Status.PAID)

    @property
    def blocking(self):
        return [i for i in self.issues if i.get("level") == "block"]

    @property
    def warnings(self):
        return [i for i in self.issues if i.get("level") == "warn"]

    @property
    def iban_masked(self):
        return f"{self.iban[:4]} \u2022\u2022\u2022\u2022 {self.iban[-4:]}" if len(self.iban) > 8 else self.iban

    def __str__(self):
        return f"Final settlement, {self.employee}"


@audited(module="payroll", company="company_id", subject="employee_id", mask=["amount"])
class SettlementItem(BaseModel):
    """One line of the statement. A positive amount is owed to the employee, a negative amount is taken from them."""

    class Kind(models.TextChoices):
        WAGES = "wages", "Salary"
        ABSENCE = "absence", "Absence"
        SIO = "sio", "Social insurance"
        LEAVE = "leave", "Annual leave"
        NOTICE = "notice", "Notice"
        GRATUITY = "gratuity", "Gratuity"
        LOAN = "loan", "Loan balance"
        ADDITION = "addition", "Addition"
        DEDUCTION = "deduction", "Deduction"

    settlement = models.ForeignKey(FinalSettlement, on_delete=models.CASCADE, related_name="items")
    kind = models.CharField(max_length=10, choices=Kind.choices)
    label = models.CharField(max_length=160)
    amount = models.DecimalField(**MONEY)
    is_system = models.BooleanField(default=False)
    source = models.CharField(max_length=10, default="manual")
    source_id = models.PositiveIntegerField(null=True, blank=True)
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["position", "id"]
        constraints = [models.UniqueConstraint(
            fields=["settlement", "source", "source_id"], condition=models.Q(source_id__isnull=False),
            name="uniq_settlement_item_source")]

    @property
    def employee_id(self):
        return self.settlement.employee_id

    @property
    def company_id(self):
        return self.settlement.company_id

    def __str__(self):
        return self.label
