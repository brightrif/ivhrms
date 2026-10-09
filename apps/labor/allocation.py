"""Where labor workers are deployed: work orders under a project, and who is allocated to which project and site.
Imported at the bottom of models.py so Django registers both models."""
from django.core.exceptions import ValidationError
from django.db import models

from apps.audit.registry import audited
from apps.core.models import BaseModel
from apps.core.scoping import CompanyQuerySet


@audited(module="labor", company="company_id")
class WorkOrder(BaseModel):
    """A contract or work order inside a project. Costs can be charged to it. Optional: an allocation may name
    only a project and a site."""

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        CLOSED = "closed", "Closed"

    company = models.ForeignKey("organization.Company", on_delete=models.PROTECT, related_name="+", editable=False)
    project = models.ForeignKey("organization.Project", on_delete=models.PROTECT, related_name="work_orders")
    code = models.CharField("Work order / contract no.", max_length=30)
    name = models.CharField(max_length=150)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=6, choices=Status.choices, default=Status.OPEN)

    objects = CompanyQuerySet.as_manager()

    class Meta:
        ordering = ["project__code", "code"]
        constraints = [
            models.UniqueConstraint(fields=["project", "code"], name="uniq_work_order_code_per_project",
                                    violation_error_message="This project already has a work order with this number."),
            models.CheckConstraint(
                condition=models.Q(end_date__isnull=True) | models.Q(start_date__isnull=True)
                | models.Q(end_date__gte=models.F("start_date")),
                name="work_order_ends_after_it_starts"),
        ]

    def save(self, *args, **kwargs):
        self.company_id = self.project.company_id
        super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValidationError({"end_date": "The end date cannot be before the start date."})

    def __str__(self):
        return f"{self.code} {self.name}"


@audited(module="labor", company="company_id", subject="employee_id")
class LaborAllocation(BaseModel):
    """Effective-dated. This table IS the deployment history: where each worker was, and when.
    The row with no end date is where the worker is now."""

    company = models.ForeignKey("organization.Company", on_delete=models.PROTECT, related_name="+", editable=False)
    employee = models.ForeignKey("employees.Employee", on_delete=models.PROTECT, related_name="labor_allocations")
    project = models.ForeignKey("organization.Project", on_delete=models.PROTECT, related_name="+")
    location = models.ForeignKey("organization.Location", on_delete=models.PROTECT, related_name="+",
                                 verbose_name="Site")
    work_order = models.ForeignKey(WorkOrder, null=True, blank=True, on_delete=models.PROTECT,
                                   related_name="allocations")
    effective_from = models.DateField()
    effective_to = models.DateField(null=True, blank=True, help_text="Last day on this site. Empty = still there.")
    notes = models.CharField(max_length=255, blank=True)
    is_main = models.BooleanField(default=False,
                                  help_text="The worker's main project: where attendance and hours start from.")

    objects = CompanyQuerySet.as_manager()

    class Meta:
        ordering = ["-effective_from", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["employee", "project", "effective_from"],
                                    name="uniq_allocation_start_per_project"),
            models.UniqueConstraint(fields=["employee", "project"], condition=models.Q(effective_to__isnull=True),
                                    name="one_open_allocation_per_worker_per_project"),
            models.UniqueConstraint(fields=["employee"],
                                    condition=models.Q(effective_to__isnull=True, is_main=True),
                                    name="one_main_allocation_per_worker"),
            models.CheckConstraint(
                condition=models.Q(effective_to__isnull=True) | models.Q(effective_to__gte=models.F("effective_from")),
                name="allocation_ends_after_it_starts"),
        ]
        indexes = [models.Index(fields=["project", "effective_to"]), models.Index(fields=["location", "effective_to"]),
                   models.Index(fields=["company", "effective_from"])]

    def save(self, *args, **kwargs):
        self.company_id = self.employee.company_id
        super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        if self.work_order_id and self.project_id and self.work_order.project_id != self.project_id:
            raise ValidationError({"work_order": "This work order belongs to another project."})

    @property
    def is_open(self):
        return self.effective_to is None

    @property
    def place(self):
        """'PRJ-1 Tower A / Site 3', plus the work order when there is one."""
        text = f"{self.project.code} {self.project.name} / {self.location.name}"
        return f"{text} / {self.work_order.code}" if self.work_order_id else text

    def __str__(self):
        return f"{self.employee.employee_no} at {self.place} from {self.effective_from}"
