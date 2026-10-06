from datetime import date, datetime

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from apps.audit.registry import audited
from apps.core.models import BaseModel


def default_weekly_off():
    return list(getattr(settings, "HRMS_DEFAULT_WEEKLY_OFF", [4]))


@audited(module="scheduling", company="company_id")
class Holiday(BaseModel):
    company = models.ForeignKey("organization.Company", null=True, blank=True,
                                on_delete=models.CASCADE, related_name="+",
                                help_text="Empty = applies to every company")
    date = models.DateField()
    name = models.CharField(max_length=120)
    name_ar = models.CharField("Name (Arabic)", max_length=120, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["date"]
        constraints = [
            models.UniqueConstraint(fields=["company", "date"], name="uniq_holiday_company_date"),
            models.UniqueConstraint(fields=["date"], condition=models.Q(company__isnull=True),
                                    name="uniq_global_holiday_date"),
        ]

    def __str__(self):
        return f"{self.date} {self.name}"


@audited(module="scheduling", company="company_id")
class Shift(BaseModel):
    class Kind(models.TextChoices):
        GENERAL = "general"
        MORNING = "morning"
        EVENING = "evening"
        NIGHT = "night"

    company = models.ForeignKey("organization.Company", on_delete=models.CASCADE, related_name="shifts")
    code = models.CharField(max_length=20)
    name = models.CharField(max_length=80)
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.GENERAL)
    start_time = models.TimeField()
    end_time = models.TimeField()
    break_minutes = models.PositiveSmallIntegerField(default=0)
    grace_minutes = models.PositiveSmallIntegerField(default=0, help_text="Late arrival allowed without being marked late")
    weekly_off_days = models.JSONField(default=default_weekly_off,
                                       help_text="Weekday numbers, Mon=0 ... Sun=6. e.g. [4] or [4, 5]")
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["company", "code"], name="uniq_shift_code_per_company")]

    @property
    def crosses_midnight(self):
        return self.end_time <= self.start_time

    @property
    def paid_minutes(self):
        start = datetime.combine(date(2000, 1, 1), self.start_time)
        end = datetime.combine(date(2000, 1, 2 if self.crosses_midnight else 1), self.end_time)
        return int((end - start).total_seconds() // 60) - self.break_minutes

    def clean(self):
        days = self.weekly_off_days
        if not isinstance(days, list) or any(not isinstance(d, int) or not 0 <= d <= 6 for d in days):
            raise ValidationError({"weekly_off_days": "Use a list of weekday numbers 0 (Mon) to 6 (Sun), e.g. [4]."})

    def __str__(self):
        return f"{self.code} {self.start_time:%H:%M}-{self.end_time:%H:%M}"


@audited(module="scheduling", subject="employee_id", company="company_id")
class ShiftAssignment(BaseModel):
    """Effective-dated. This table IS the shift change history."""
    company = models.ForeignKey("organization.Company", on_delete=models.PROTECT, related_name="+", editable=False)
    employee = models.ForeignKey("employees.Employee", on_delete=models.CASCADE, related_name="shift_assignments")
    shift = models.ForeignKey(Shift, on_delete=models.PROTECT, related_name="assignments")
    effective_from = models.DateField()
    effective_to = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["-effective_from"]
        constraints = [
            models.UniqueConstraint(fields=["employee", "effective_from"], name="uniq_shift_assign_start"),
            models.UniqueConstraint(fields=["employee"], condition=models.Q(effective_to__isnull=True),
                                    name="one_open_shift_assignment"),
        ]

    def save(self, *args, **kwargs):
        self.company_id = self.employee.company_id
        super().save(*args, **kwargs)