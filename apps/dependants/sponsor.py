"""Who may have a new family visa: the decision HR or Management made about one employee.

Imported at the bottom of models.py. No row, or a row whose mode is "default", means: follow the designation."""
from django.db import models

from apps.audit.registry import audited
from apps.core.models import BaseModel


@audited(module="dependants", company="company_id", subject="employee_id")
class FamilyVisaException(BaseModel):
    class Mode(models.TextChoices):
        DEFAULT = "default", "Default"
        ALLOWED = "allowed", "Allowed"
        NOT_ALLOWED = "not_allowed", "Not allowed"

    company = models.ForeignKey("organization.Company", on_delete=models.PROTECT, related_name="+")
    employee = models.OneToOneField("employees.Employee", on_delete=models.PROTECT,
                                    related_name="family_visa_exception")
    mode = models.CharField(max_length=12, choices=Mode.choices, default=Mode.DEFAULT)
    reason = models.TextField(blank=True)

    def save(self, *args, **kwargs):
        self.company_id = self.employee.company_id            # the company always follows the employee
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.employee} - {self.get_mode_display()}"
