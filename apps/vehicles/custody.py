"""What happens to a vehicle when the employee holding it goes on notice or leaves. Imported at the bottom of models.py."""
from django.db import models
from django.utils import timezone

from apps.audit.registry import audited
from apps.core.models import BaseModel
from apps.core.scoping import CompanyQuerySet


@audited(module="vehicles", company="company_id", subject="employee_id")
class CustodyRequest(BaseModel):
    """A request, sent through the approval workflow, to decide what happens to a vehicle held by someone who is
    leaving. Approval only authorises the decision; HR then carries it out (the hand-over or the return), so the
    date and the odometer are the real ones."""

    class Decision(models.TextChoices):
        HANDOVER = "handover", "Hand over to someone else"
        KEEP = "keep", "Keep with the employee"
        RETURN = "return", "Return to the company"

    class Status(models.TextChoices):
        PENDING = "pending", "Waiting for approval"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        CANCELLED = "cancelled", "Cancelled"

    company = models.ForeignKey("organization.Company", on_delete=models.PROTECT, related_name="+", editable=False)
    vehicle = models.ForeignKey("vehicles.Vehicle", on_delete=models.PROTECT, related_name="custody_requests")
    assignment = models.ForeignKey("vehicles.VehicleAssignment", on_delete=models.PROTECT,
                                   related_name="custody_requests")
    employee = models.ForeignKey("employees.Employee", on_delete=models.PROTECT,
                                 related_name="vehicle_custody_requests", help_text="The person holding the vehicle.")
    decision = models.CharField(max_length=10, choices=Decision.choices)
    new_driver = models.ForeignKey("employees.Employee", null=True, blank=True, on_delete=models.PROTECT,
                                   related_name="+")
    planned_on = models.DateField("When", null=True, blank=True,
                                  help_text="For example the last working day. Only a plan: HR records the real date.")
    reason = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)

    objects = CompanyQuerySet.as_manager()

    class Meta:
        ordering = ["-id"]
        constraints = [
            models.UniqueConstraint(fields=["assignment"], condition=models.Q(status="pending"),
                                    name="one_pending_custody_request_per_assignment",
                                    violation_error_message="A decision for this vehicle is already waiting for approval."),
        ]

    def save(self, *args, **kwargs):
        self.company_id = self.vehicle.company_id
        super().save(*args, **kwargs)

    @property
    def title(self):
        plate = self.vehicle.plate_number
        if self.decision == self.Decision.HANDOVER:
            return f"{plate}: hand over to {self.new_driver.full_name}"
        if self.decision == self.Decision.KEEP:
            return f"{plate}: {self.employee.full_name} keeps the vehicle"
        return f"{plate}: return to the company"

    @property
    def detail(self):
        emp = self.employee
        where = "has left" if emp.status == "separated" else "is on notice" if emp.status == "on_notice" else "is active"
        when = f", planned for {self.planned_on:%d %b %Y}" if self.planned_on else ""
        return f"{emp.full_name} {where}{when}"

    def __str__(self):
        return self.title


class LeaverAlertLog(models.Model):
    """Which 'this person holds a vehicle' notice went out. The unique key stops repeated emails."""
    assignment = models.ForeignKey("vehicles.VehicleAssignment", on_delete=models.CASCADE, related_name="leaver_alerts")
    state = models.CharField(max_length=6)                    # "notice" or "left"
    sent_at = models.DateTimeField(default=timezone.now)
    recipients = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["-sent_at"]
        constraints = [models.UniqueConstraint(fields=["assignment", "state"], name="uniq_leaver_alert_per_state")]
