"""Air ticket requests and who travels on them. Imported at the bottom of models.py."""
from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models

from apps.audit.registry import audited
from apps.core.models import BaseModel
from apps.core.scoping import CompanyQuerySet

from .models import TicketEntitlement, TicketPolicy

_MONEY = dict(max_digits=12, decimal_places=3, validators=[MinValueValidator(Decimal("0"))])


@audited(module="airtickets", company="company_id", subject="employee_id")
class TicketRequest(BaseModel):
    """One trip. Approval is handled by the approval engine; this row follows the outcome and then tracks the
    booking (or the allowance) and the travel."""

    class Status(models.TextChoices):
        PENDING = "pending", "Waiting for approval"
        APPROVED = "approved", "Approved"
        BOOKED = "booked", "Tickets issued"
        COMPLETED = "completed", "Travelled"
        PAID = "paid", "Allowance paid"
        REJECTED = "rejected", "Rejected"
        CANCELLED = "cancelled", "Cancelled"

    OPEN = (Status.PENDING, Status.APPROVED, Status.BOOKED)          # still in progress

    company = models.ForeignKey("organization.Company", on_delete=models.PROTECT, related_name="+")
    employee = models.ForeignKey("employees.Employee", on_delete=models.PROTECT, related_name="ticket_requests")
    entitlement = models.ForeignKey(TicketEntitlement, on_delete=models.PROTECT, related_name="requests")
    mode = models.CharField(max_length=10, choices=TicketPolicy.Mode.choices)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    depart_on = models.DateField("Departure")
    return_on = models.DateField("Return", null=True, blank=True)
    destination = models.CharField(max_length=80, blank=True)
    notes = models.TextField(blank=True)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    allowance_total = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True,
                                          help_text="The allowance for everyone travelling (allowance mode only).")
    # issuing the tickets (company-booked)
    airline = models.CharField(max_length=80, blank=True)
    booking_ref = models.CharField("Booking reference", max_length=40, blank=True)
    issued_on = models.DateField(null=True, blank=True)
    employee_share = models.DecimalField(max_digits=12, decimal_places=3, default=Decimal("0"),
                                         validators=[MinValueValidator(Decimal("0"))],
                                         help_text="Part of the fare the employee pays (an upgrade, an extra bag...).")
    # what happened
    departed_on = models.DateField(null=True, blank=True)
    returned_on = models.DateField(null=True, blank=True)
    paid_on = models.DateField(null=True, blank=True)
    payment_ref = models.CharField(max_length=60, blank=True)
    cancel_reason = models.TextField(blank=True)

    objects = CompanyQuerySet.as_manager()

    class Meta:
        ordering = ["-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["employee"], condition=models.Q(status__in=["pending", "approved", "booked"]),
                name="uniq_open_ticket_request_per_employee",
                violation_error_message="This employee already has an air ticket request in progress."),
        ]
        indexes = [models.Index(fields=["company", "status"])]

    @property
    def total_fare(self):
        return sum((t.fare or Decimal("0") for t in self.travellers.all()), Decimal("0"))

    @property
    def company_cost(self):
        """What the company pays: the fares less the part the employee pays."""
        return self.total_fare - (self.employee_share or Decimal("0"))

    @property
    def title(self):
        return f"Air ticket: {self.employee.full_name}, {self.depart_on:%d %b %Y}"

    @property
    def detail(self):
        n = self.travellers.count()
        where = f" to {self.destination}" if self.destination else ""
        return f"{n} traveller{'s' if n != 1 else ''}{where}, {self.depart_on:%d %b %Y}" + (
            f" to {self.return_on:%d %b %Y}" if self.return_on else " (one way)")

    def __str__(self):
        return self.title


class TicketTraveller(BaseModel):
    """A person on a request: the employee, then any family covered by the policy. The name is copied so the record
    still reads correctly if the family member is changed later."""

    request = models.ForeignKey(TicketRequest, on_delete=models.CASCADE, related_name="travellers")
    dependant = models.ForeignKey("dependants.Dependant", null=True, blank=True, on_delete=models.PROTECT,
                                  related_name="+")
    name = models.CharField(max_length=160)
    relationship = models.CharField(max_length=10)                  # employee, spouse, child
    ticket_no = models.CharField(max_length=30, blank=True)
    fare = models.DecimalField(null=True, blank=True, **_MONEY)

    class Meta:
        ordering = ["id"]
        constraints = [models.UniqueConstraint(fields=["request", "dependant"],
                                               condition=models.Q(dependant__isnull=False),
                                               name="uniq_traveller_per_request")]

    def __str__(self):
        return f"{self.name} ({self.relationship})"
