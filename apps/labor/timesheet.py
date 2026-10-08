"""Hours worked by labor, one entry per worker per day. Imported at the bottom of models.py so Django registers it."""
from decimal import Decimal

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from apps.audit.registry import audited
from apps.core.models import BaseModel
from apps.core.scoping import CompanyQuerySet


@audited(module="labor", company="company_id", subject="employee_id")
class TimeEntry(BaseModel):
    """The hours a worker put in on a day, charged to the project, site and work order they were allocated to.

    Overtime is simply the hours above what that day expected: a full day expects the worker's standard hours, a
    half day half of them. Both numbers are copied onto the entry, so changing someone's pay terms later never
    changes an old timesheet. A confirmed entry is locked."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        CONFIRMED = "confirmed", "Confirmed"

    company = models.ForeignKey("organization.Company", on_delete=models.PROTECT, related_name="+", editable=False)
    employee = models.ForeignKey("employees.Employee", on_delete=models.PROTECT, related_name="labor_time_entries")
    date = models.DateField()
    project = models.ForeignKey("organization.Project", on_delete=models.PROTECT, related_name="+")
    location = models.ForeignKey("organization.Location", on_delete=models.PROTECT, related_name="+")
    work_order = models.ForeignKey("labor.WorkOrder", null=True, blank=True, on_delete=models.PROTECT,
                                   related_name="time_entries")
    hours = models.DecimalField(max_digits=4, decimal_places=2,
                                validators=[MinValueValidator(Decimal("0.25")), MaxValueValidator(Decimal("24"))])
    expected_hours = models.DecimalField(max_digits=4, decimal_places=2,
                                         help_text="What the day expected: standard hours, or half for a half day.")
    overtime_eligible = models.BooleanField(default=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)

    objects = CompanyQuerySet.as_manager()

    class Meta:
        ordering = ["-date", "employee_id"]
        constraints = [
            models.UniqueConstraint(fields=["employee", "date"], name="uniq_time_entry_employee_date"),
            models.CheckConstraint(condition=models.Q(hours__gt=0, hours__lte=24), name="time_entry_hours_in_range"),
        ]
        indexes = [models.Index(fields=["project", "date"]), models.Index(fields=["location", "date"]),
                   models.Index(fields=["work_order", "date"]), models.Index(fields=["company", "status", "date"])]

    def save(self, *args, **kwargs):
        self.company_id = self.employee.company_id
        super().save(*args, **kwargs)

    @property
    def is_confirmed(self):
        return self.status == self.Status.CONFIRMED

    @property
    def regular_hours(self):
        return min(self.hours, self.expected_hours)

    @property
    def overtime_hours(self):
        """Hours beyond the day's expected hours, if the worker is eligible for overtime (else 0)."""
        return max(self.hours - self.expected_hours, Decimal("0")) if self.overtime_eligible else Decimal("0")

    def __str__(self):
        return f"{self.employee.employee_no} {self.date} {self.hours}h"
