"""Loans and salary advances. Imported at the bottom of models.py.

A loan is paid out once, then repaid by instalments that payroll deducts. The instalments are the schedule; what has
actually been repaid is kept as payments, and the balance is the total minus every payment. Payroll picks the instalments
up next to the fines and renewal fees it already collects, and the final settlement recovers what is left."""
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Sum
from django.db.models.signals import post_migrate
from django.dispatch import receiver

from apps.audit.registry import audited
from apps.core.models import BaseModel
from apps.core.scoping import CompanyQuerySet

MONEY = dict(max_digits=12, decimal_places=3)
ZERO = Decimal("0")


@audited(module="payroll", company="company_id", subject="employee_id", mask=["amount", "interest_total"])
class Loan(BaseModel):
    class Kind(models.TextChoices):
        LOAN = "loan", "Loan"
        ADVANCE = "advance", "Salary advance"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SUBMITTED = "submitted", "Waiting for approval"
        APPROVED = "approved", "Approved, not paid out"
        ACTIVE = "active", "Active"
        CLOSED = "closed", "Closed"
        CANCELLED = "cancelled", "Cancelled"

    company = models.ForeignKey("organization.Company", on_delete=models.PROTECT, related_name="+", editable=False)
    employee = models.ForeignKey("employees.Employee", on_delete=models.PROTECT, related_name="loans")
    kind = models.CharField(max_length=8, choices=Kind.choices, default=Kind.LOAN)
    amount = models.DecimalField("Amount", **MONEY)
    months = models.PositiveSmallIntegerField("Instalments", default=1)
    interest_rate = models.DecimalField("Yearly interest %", max_digits=5, decimal_places=2, default=ZERO,
                                        help_text="Flat, on the amount, for the whole term. 0 means no interest.")
    interest_total = models.DecimalField(default=ZERO, **MONEY)
    first_month = models.DateField("First deduction month")
    reason = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    agreed_by = models.CharField("Agreed verbally by", max_length=120, blank=True)
    agreed_on = models.DateField("Agreed on", null=True, blank=True)
    override_reason = models.CharField("Why a limit was passed", max_length=255, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    paid_out_on = models.DateField(null=True, blank=True)
    payout_reference = models.CharField(max_length=60, blank=True)
    closed_on = models.DateField(null=True, blank=True)
    closed_note = models.CharField(max_length=255, blank=True)
    prepared_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="+")
    note = models.CharField(max_length=500, blank=True)

    objects = CompanyQuerySet.as_manager()

    class Meta:
        ordering = ["-id"]
        permissions = [("submit_loan", "Can send loans for approval"),
                       ("pay_loan", "Can record loan payouts and repayments"),
                       ("agree_loan", "Can add a loan that was agreed verbally")]

    def save(self, *args, **kwargs):
        self.company_id = self.employee.company_id
        super().save(*args, **kwargs)

    @property
    def number(self):
        return f"LN-{self.pk:04d}" if self.pk else "New"

    @property
    def total(self):
        return self.amount + self.interest_total

    @property
    def repaid(self):
        return self.payments.aggregate(s=Sum("amount"))["s"] or ZERO

    @property
    def balance(self):
        return self.total - self.repaid

    @property
    def verbal(self):
        return bool(self.agreed_by)

    @property
    def title(self):
        return f"{self.get_kind_display()} {self.number}: {self.amount:,.3f} BHD for {self.employee.full_name}"

    @property
    def detail(self):
        return f"{self.months} instalment{'s' if self.months != 1 else ''} from {self.first_month:%b %Y}"

    def __str__(self):
        return f"{self.number}, {self.employee}"


@audited(module="payroll", company="company_id", subject="employee_id", mask=["amount", "original_amount"])
class LoanInstalment(BaseModel):
    class Status(models.TextChoices):
        SCHEDULED = "scheduled", "Scheduled"
        DEDUCTED = "deducted", "Deducted"
        SKIPPED = "skipped", "Skipped"
        COVERED = "covered", "Paid early"
        REPLACED = "replaced", "Replaced by a new schedule"

    loan = models.ForeignKey(Loan, on_delete=models.CASCADE, related_name="instalments")
    due_month = models.DateField()
    amount = models.DecimalField(**MONEY)
    original_amount = models.DecimalField(**MONEY)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.SCHEDULED)
    run = models.ForeignKey("payroll.PayrollRun", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    deducted_on = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["due_month", "id"]

    @property
    def employee_id(self):
        return self.loan.employee_id

    @property
    def company_id(self):
        return self.loan.company_id

    def __str__(self):
        return f"{self.loan.number} {self.due_month:%b %Y}"


@audited(module="payroll", company="company_id", subject="employee_id", mask=["amount"])
class LoanPayment(BaseModel):
    """Anything repaid: an instalment taken in payroll, an early payment, the recovery at exit, or a waiver."""

    class Method(models.TextChoices):
        PAYROLL = "payroll", "Deducted in payroll"
        BANK = "bank", "Bank transfer"
        CASH = "cash", "Cash"
        SETTLEMENT = "settlement", "Final settlement"
        WAIVED = "waived", "Waived"

    loan = models.ForeignKey(Loan, on_delete=models.CASCADE, related_name="payments")
    paid_on = models.DateField()
    amount = models.DecimalField(**MONEY)
    method = models.CharField(max_length=10, choices=Method.choices)
    reference = models.CharField(max_length=80, blank=True)
    note = models.CharField(max_length=255, blank=True)
    instalment = models.ForeignKey(LoanInstalment, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="+")

    class Meta:
        ordering = ["paid_on", "id"]

    @property
    def employee_id(self):
        return self.loan.employee_id

    @property
    def company_id(self):
        return self.loan.company_id

    def __str__(self):
        return f"{self.loan.number} {self.amount} on {self.paid_on}"


# HR keeps the loans; Management can see them. Given once per model, the first time its rights are missing from the group,
# so anything changed later on the Access tab survives future migrations. The approval is one step: anyone in Management.
RIGHTS = {
    "HR": {"loan": ["view", "add", "change", "submit", "pay", "agree"]},
    "Management": {"loan": ["view"]},
}
FLOW_CODE = "payroll.loan"


@receiver(post_migrate)
def give_hr_the_loan_rights(sender, using="default", **kwargs):
    if sender.label != "payroll":
        return
    from django.apps import apps as global_apps
    from django.contrib.auth.management import create_permissions
    from django.contrib.auth.models import Group, Permission

    from apps.core.models import ApprovalFlow, ApprovalStep

    create_permissions(sender, verbosity=0, using=using, apps=kwargs.get("apps", global_apps))
    management, _ = Group.objects.using(using).get_or_create(name="Management")
    if not ApprovalFlow.objects.using(using).filter(code=FLOW_CODE, company__isnull=True).exists():
        # bulk_create sends no save signals, on purpose: this is system data, not something a person did
        flow = ApprovalFlow.objects.using(using).bulk_create([ApprovalFlow(code=FLOW_CODE, name="Loan approval")])[0]
        ApprovalStep.objects.using(using).bulk_create([ApprovalStep(
            flow=flow, order=1, name="Management approval", approver_type=ApprovalStep.ApproverType.GROUP,
            group=management)])
    for group_name, models_ in RIGHTS.items():
        group, _ = Group.objects.using(using).get_or_create(name=group_name)
        for model, actions in models_.items():
            perms = Permission.objects.using(using).filter(content_type__app_label="payroll", content_type__model=model)
            if not perms.exists() or group.permissions.filter(pk__in=perms).exists():
                continue
            group.permissions.add(*perms.filter(codename__in=[f"{a}_{model}" for a in actions]))
