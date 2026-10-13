"""Remembers which reminders were sent. Imported at the bottom of models.py."""
from django.db import models

from apps.core.models import BaseModel

from .models import TicketEntitlement


class TicketReminderLog(BaseModel):
    """One row per entitlement, stage and due date, so a reminder goes out once. If the due date moves (a policy or a
    joining date changed) the new date is reminded about again."""

    class Stage(models.TextChoices):
        LEAD = "lead", "Coming up"
        DUE = "due", "Due now"

    entitlement = models.ForeignKey(TicketEntitlement, on_delete=models.CASCADE, related_name="reminders")
    stage = models.CharField(max_length=4, choices=Stage.choices)
    due_on = models.DateField()
    recipients = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["entitlement", "stage", "due_on"],
                                               name="uniq_ticket_reminder")]
