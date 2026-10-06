from django.db import models

from apps.audit.registry import audited
from apps.core.models import BaseModel


class LeaveType(BaseModel):
    company = models.ForeignKey("organization.Company", on_delete=models.CASCADE, related_name="leave_types")
    code = models.CharField(max_length=20)
    name = models.CharField(max_length=80)
    name_ar = models.CharField("Name (Arabic)", max_length=80, blank=True)
    is_paid = models.BooleanField(default=True)
    tracks_balance = models.BooleanField(default=False, help_text="Enforce a balance. Off = unlimited, HR monitors")
    annual_entitlement = models.DecimalField(max_digits=5, decimal_places=1, default=0)
    allow_half_day = models.BooleanField(default=True)
    requires_attachment = models.BooleanField(default=False)
    exclude_holidays = models.BooleanField(default=True, help_text="Public holidays inside the range are not charged")
    exclude_weekly_offs = models.BooleanField(default=False, help_text="Weekly offs inside the range are not charged")
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["company", "code"], name="uniq_leave_type_code")]

    def __str__(self):
        return f"{self.name} ({self.company.code})"


@audited(module="leave", subject="employee_id", company="company_id")
class LeaveRequest(BaseModel):
    class Status(models.TextChoices):
        PENDING = "pending"
        APPROVED = "approved"
        REJECTED = "rejected"
        CANCELLED = "cancelled"

    company = models.ForeignKey("organization.Company", on_delete=models.PROTECT, related_name="+", editable=False)
    employee = models.ForeignKey("employees.Employee", on_delete=models.PROTECT, related_name="leave_requests")
    leave_type = models.ForeignKey(LeaveType, on_delete=models.PROTECT, related_name="+")
    start_date = models.DateField()
    end_date = models.DateField()
    is_half_day = models.BooleanField(default=False)
    days = models.DecimalField(max_digits=5, decimal_places=1)
    reason = models.TextField(blank=True)
    attachment = models.FileField(upload_to="leave/%Y/%m/", blank=True)   # move to private storage in the documents phase
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)

    class Meta:
        ordering = ["-start_date"]
        permissions = [("cancel_any_leave", "Can cancel any approved leave")]
        constraints = [models.CheckConstraint(condition=models.Q(end_date__gte=models.F("start_date")),
                                              name="leave_end_after_start")]
        indexes = [models.Index(fields=["employee", "start_date"]),
                   models.Index(fields=["company", "status"])]

    def save(self, *args, **kwargs):
        self.company_id = self.employee.company_id
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.employee.employee_no} {self.leave_type.code} {self.start_date}..{self.end_date}"


@audited(module="leave", subject="employee_id", company="company_id")
class LeaveLedger(BaseModel):
    """One signed row per balance event. Balance = sum of rows."""
    class Kind(models.TextChoices):
        ENTITLEMENT = "entitlement"
        CARRY_FORWARD = "carry_forward"
        TAKEN = "taken"
        REVERSAL = "reversal"
        ADJUSTMENT = "adjustment"

    company = models.ForeignKey("organization.Company", on_delete=models.PROTECT, related_name="+", editable=False)
    employee = models.ForeignKey("employees.Employee", on_delete=models.PROTECT, related_name="leave_ledger")
    leave_type = models.ForeignKey(LeaveType, on_delete=models.PROTECT, related_name="+")
    year = models.PositiveSmallIntegerField()
    kind = models.CharField(max_length=15, choices=Kind.choices)
    days = models.DecimalField(max_digits=6, decimal_places=1, help_text="+ adds to the balance, - deducts")
    request = models.ForeignKey(LeaveRequest, null=True, blank=True, on_delete=models.PROTECT,
                                related_name="ledger_entries")
    remarks = models.CharField(max_length=255, blank=True)

    class Meta:
        indexes = [models.Index(fields=["employee", "leave_type", "year"])]

    def save(self, *args, **kwargs):
        self.company_id = self.employee.company_id
        super().save(*args, **kwargs)