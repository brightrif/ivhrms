"""Overtime: the company's rules, and the claims worked out from confirmed hours.
Imported at the bottom of models.py so Django registers both models."""
from decimal import Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from apps.audit.registry import audited
from apps.core.models import BaseModel
from apps.core.scoping import CompanyQuerySet


def _multiplier():
    return [MinValueValidator(Decimal("1")), MaxValueValidator(Decimal("5"))]


@audited(module="labor", company="company_id")
class OvertimePolicy(BaseModel):
    """How overtime is paid. Effective-dated like pay rates: a new row ends the old one the day before, and a claim
    keeps pointing at the rules it was worked out under. Nothing is assumed: a company has no rules until someone
    sets them."""

    company = models.ForeignKey("organization.Company", on_delete=models.PROTECT, related_name="overtime_policies")
    overtime_applies = models.BooleanField(
        "This company pays overtime", default=True,
        help_text="Untick if the company does not pay overtime. Hours above the standard day are still recorded, "
                  "but nothing is claimed, and the rates below are ignored.")
    working_day_multiplier = models.DecimalField("Normal working day", max_digits=4, decimal_places=2,
                                                 validators=_multiplier(), default=Decimal("1.25"),
                                                 help_text="Pay for each overtime hour, as a multiple of the hourly rate.")
    weekly_off_multiplier = models.DecimalField("Weekly off day", max_digits=4, decimal_places=2,
                                                validators=_multiplier(), default=Decimal("1.50"))
    holiday_multiplier = models.DecimalField("Public holiday", max_digits=4, decimal_places=2,
                                             validators=_multiplier(), default=Decimal("1.50"))
    all_hours_on_days_off = models.BooleanField(
        "Every hour on a day off is overtime", default=True,
        help_text="On a weekly off or a holiday, count all hours worked as overtime, not only those beyond the "
                  "standard day. Payroll must then not also pay that day as a normal working day.")
    monthly_divisor = models.PositiveSmallIntegerField(
        "Days in a month", default=30, validators=[MinValueValidator(20), MaxValueValidator(31)],
        help_text="Turns a monthly salary into a day rate: salary / this number / standard hours = hourly rate.")
    effective_from = models.DateField()
    effective_to = models.DateField(null=True, blank=True)

    objects = CompanyQuerySet.as_manager()

    class Meta:
        ordering = ["company", "-effective_from"]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "effective_from"], name="uniq_overtime_policy_start",
                violation_error_message="Rules already start on this date. The new rules must start after the current ones."),
            models.UniqueConstraint(fields=["company"], condition=models.Q(effective_to__isnull=True),
                                    name="one_open_overtime_policy"),
            models.CheckConstraint(
                condition=models.Q(effective_to__isnull=True) | models.Q(effective_to__gte=models.F("effective_from")),
                name="overtime_policy_ends_after_it_starts"),
        ]

    def multiplier_for(self, kind):
        return {"weekly_off": self.weekly_off_multiplier, "holiday": self.holiday_multiplier}.get(
            kind, self.working_day_multiplier)

    def __str__(self):
        what = "overtime rules" if self.overtime_applies else "no overtime"
        return f"{self.company.code} {what} from {self.effective_from}"


@audited(module="labor", company="company_id", subject="employee_id")
class OvertimeClaim(BaseModel):
    """Overtime for one worker on one day, worked out from a confirmed time entry. The rate, multiplier and hours are
    copied here, so later changes to pay or rules never alter a claim. Approved claims are what payroll will read."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    class DayKind(models.TextChoices):
        WORKING = "working", "Working day"
        WEEKLY_OFF = "weekly_off", "Weekly off"
        HOLIDAY = "holiday", "Holiday"

    company = models.ForeignKey("organization.Company", on_delete=models.PROTECT, related_name="+", editable=False)
    entry = models.OneToOneField("labor.TimeEntry", on_delete=models.PROTECT, related_name="overtime_claim")
    employee = models.ForeignKey("employees.Employee", on_delete=models.PROTECT, related_name="labor_overtime_claims")
    date = models.DateField()
    project = models.ForeignKey("organization.Project", on_delete=models.PROTECT, related_name="+")
    location = models.ForeignKey("organization.Location", on_delete=models.PROTECT, related_name="+")
    work_order = models.ForeignKey("labor.WorkOrder", null=True, blank=True, on_delete=models.PROTECT,
                                   related_name="overtime_claims")
    day_kind = models.CharField(max_length=10, choices=DayKind.choices)
    hours = models.DecimalField("Overtime hours", max_digits=4, decimal_places=2)
    hourly_rate = models.DecimalField(max_digits=10, decimal_places=4)
    multiplier = models.DecimalField(max_digits=4, decimal_places=2)
    amount = models.DecimalField("Amount (BHD)", max_digits=10, decimal_places=3)
    policy = models.ForeignKey(OvertimePolicy, on_delete=models.PROTECT, related_name="claims")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, editable=False,
                                   on_delete=models.SET_NULL, related_name="+")
    decided_at = models.DateTimeField(null=True, blank=True, editable=False)
    decision_note = models.CharField(max_length=255, blank=True)

    objects = CompanyQuerySet.as_manager()

    class Meta:
        ordering = ["-date", "employee_id"]
        constraints = [models.CheckConstraint(condition=models.Q(hours__gt=0), name="overtime_claim_has_hours")]
        indexes = [models.Index(fields=["project", "date"]), models.Index(fields=["location", "date"]),
                   models.Index(fields=["company", "status", "date"])]

    def save(self, *args, **kwargs):
        self.company_id = self.employee.company_id
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.employee.employee_no} {self.date} {self.hours}h {self.amount}"
