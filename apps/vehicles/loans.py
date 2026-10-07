"""Bank loans on vehicles and their installments. Imported at the bottom of models.py.

Only a bank loan (a fixed number of installments to a lender) is modelled. A lease is different enough
(no ownership, rent instead of principal and profit, return conditions) to be added later as its own model."""
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone

from apps.audit.registry import audited
from apps.core.models import BaseModel
from apps.core.scoping import CompanyQuerySet


@audited(module="vehicles", company="company_id")
class VehicleLoan(BaseModel):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        COMPLETED = "completed", "Fully paid"
        SETTLED = "settled", "Settled early"

    company = models.ForeignKey("organization.Company", on_delete=models.PROTECT, related_name="+",
                                editable=False)
    vehicle = models.ForeignKey("vehicles.Vehicle", on_delete=models.PROTECT, related_name="loans")
    lender = models.CharField("Bank / lender", max_length=100)
    account_no = models.CharField("Loan / account number", max_length=60, blank=True)
    down_payment = models.DecimalField("Down payment (BHD)", max_digits=12, decimal_places=3, default=0,
                                       validators=[MinValueValidator(0)])
    financed_amount = models.DecimalField("Amount financed (BHD)", max_digits=12, decimal_places=3,
                                          validators=[MinValueValidator(0)])
    installment_count = models.PositiveSmallIntegerField(
        "Number of installments", validators=[MinValueValidator(1), MaxValueValidator(120)])
    installment_amount = models.DecimalField("Installment (BHD)", max_digits=12, decimal_places=3,
                                             validators=[MinValueValidator(0)])
    final_installment_amount = models.DecimalField(
        "Last installment (BHD)", max_digits=12, decimal_places=3, null=True, blank=True,
        validators=[MinValueValidator(0)], help_text="Only if the last installment differs from the others.")
    first_due_on = models.DateField("First installment due on")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    settled_on = models.DateField(null=True, blank=True)
    settlement_amount = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True,
                                            validators=[MinValueValidator(0)])
    settlement_reference = models.CharField(max_length=60, blank=True)
    notes = models.TextField(blank=True)
    is_voided = models.BooleanField(default=False)
    voided_reason = models.CharField(max_length=200, blank=True)

    objects = CompanyQuerySet.as_manager()

    class Meta:
        ordering = ["-first_due_on", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["vehicle"], condition=models.Q(status="active", is_voided=False),
                name="one_active_loan_per_vehicle",
                violation_error_message="This vehicle already has an active loan."),
            models.CheckConstraint(condition=models.Q(financed_amount__gt=0), name="loan_financed_positive"),
            models.CheckConstraint(condition=models.Q(installment_amount__gt=0), name="loan_installment_positive"),
            models.CheckConstraint(
                condition=~models.Q(status="settled") | models.Q(settled_on__isnull=False),
                name="loan_settled_has_a_date"),
        ]

    def save(self, *args, **kwargs):
        self.company_id = self.vehicle.company_id
        super().save(*args, **kwargs)

    @property
    def scheduled_total(self):
        """Everything the installments add up to."""
        last = self.final_installment_amount if self.final_installment_amount is not None else self.installment_amount
        return self.installment_amount * (self.installment_count - 1) + last

    @property
    def cost_of_finance(self):
        """What the lender charges on top of the amount financed (interest or profit, plus any fees in the installments)."""
        return self.scheduled_total - self.financed_amount

    @property
    def purchase_price(self):
        return self.down_payment + self.financed_amount

    def __str__(self):
        return f"{self.vehicle.plate_number} loan from {self.lender}"


@audited(module="vehicles", company="company_id")
class LoanInstallment(BaseModel):
    class Status(models.TextChoices):
        DUE = "due", "Due"
        PAID = "paid", "Paid"
        SETTLED = "settled", "Cleared by early settlement"

    company = models.ForeignKey("organization.Company", on_delete=models.PROTECT, related_name="+",
                                editable=False)
    loan = models.ForeignKey(VehicleLoan, on_delete=models.PROTECT, related_name="installments")
    number = models.PositiveSmallIntegerField()
    due_on = models.DateField()
    amount = models.DecimalField(max_digits=12, decimal_places=3, validators=[MinValueValidator(0)])
    status = models.CharField(max_length=7, choices=Status.choices, default=Status.DUE)
    paid_on = models.DateField(null=True, blank=True)
    paid_amount = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True,
                                      validators=[MinValueValidator(0)])
    reference = models.CharField(max_length=60, blank=True)

    objects = CompanyQuerySet.as_manager()

    class Meta:
        ordering = ["loan", "number"]
        permissions = [("pay_loaninstallment", "Can record loan installment payments")]
        constraints = [
            models.UniqueConstraint(fields=["loan", "number"], name="uniq_installment_number_per_loan"),
            models.CheckConstraint(condition=~models.Q(status="paid") | models.Q(paid_on__isnull=False),
                                   name="installment_paid_has_a_date"),
        ]
        indexes = [models.Index(fields=["status", "due_on"])]

    def save(self, *args, **kwargs):
        self.company_id = self.loan.company_id
        super().save(*args, **kwargs)

    @property
    def is_overdue(self):
        return self.status == self.Status.DUE and self.due_on < timezone.localdate()

    def __str__(self):
        return f"{self.loan} #{self.number} due {self.due_on}"


class LoanAlertLog(models.Model):
    """Which installment alert went out. The unique key is what stops duplicate emails."""
    installment = models.ForeignKey(LoanInstallment, on_delete=models.CASCADE, related_name="alerts")
    state = models.CharField(max_length=8)                    # "due" (coming up) or "expired" (overdue)
    sent_at = models.DateTimeField(default=timezone.now)
    recipients = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["-sent_at"]
        constraints = [models.UniqueConstraint(fields=["installment", "state"], name="uniq_alert_per_installment_state")]
