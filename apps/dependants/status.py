"""A request for family status. Imported at the bottom of models.py.

The employee asks (or HR asks for them). An employee's request waits for HR; HR sends it to the reporting manager
(the approval engine), or declines it with a reason. HR's own request goes straight to the manager. When the manager
approves, the person has family status (see eligibility.py)."""
from django.conf import settings
from django.db import models

from apps.audit.registry import audited
from apps.core.models import BaseModel
from apps.core.scoping import CompanyQuerySet


@audited(module="dependants", company="company_id", subject="employee_id")
class FamilyStatusRequest(BaseModel):
    class Status(models.TextChoices):
        WAITING_HR = "waiting_hr", "Waiting for HR"
        PENDING = "pending", "Waiting for the manager"
        APPROVED = "approved", "Approved"
        DECLINED = "declined", "Declined by HR"
        REJECTED = "rejected", "Rejected by the manager"
        WITHDRAWN = "withdrawn", "Withdrawn"

    OPEN = (Status.WAITING_HR, Status.PENDING)

    company = models.ForeignKey("organization.Company", on_delete=models.PROTECT, related_name="+")
    employee = models.ForeignKey("employees.Employee", on_delete=models.PROTECT,
                                 related_name="family_status_requests")
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    from_employee = models.BooleanField(default=False, help_text="The employee asked, so HR reviews it first.")
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.WAITING_HR)
    reason = models.TextField(blank=True, help_text="Why family status is needed.")
    hr_note = models.TextField(blank=True, help_text="Why HR declined.")
    decided_note = models.TextField(blank=True, help_text="The manager's comment.")

    objects = CompanyQuerySet.as_manager()

    class Meta:
        ordering = ["-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["employee"], condition=models.Q(status__in=["waiting_hr", "pending"]),
                name="uniq_open_family_status_request",
                violation_error_message="This employee already has a family status request in progress."),
        ]
        indexes = [models.Index(fields=["company", "status"])]

    @property
    def title(self):
        return f"Family status for {self.employee.full_name}"

    @property
    def detail(self):
        who = "the employee" if self.from_employee else (self.requested_by.get_full_name() or self.requested_by.get_username())
        return f"{self.employee.employee_no}. Asked by {who}"

    def save(self, *args, **kwargs):
        self.company_id = self.employee.company_id
        super().save(*args, **kwargs)

    def __str__(self):
        return self.title
