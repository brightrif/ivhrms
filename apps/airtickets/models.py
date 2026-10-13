from decimal import Decimal

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from apps.audit.registry import audited
from apps.core.models import BaseModel
from apps.core.scoping import CompanyQuerySet

from .nationalities import key as nationality_key


@audited(module="airtickets", company="company_id")
class TicketPolicy(BaseModel):
    """Who gets an air ticket, how often and what it covers. Blank means 'any': a blank nationality list is every
    nationality, no minimum grade is every grade, no contract types is every contract. When several policies fit a
    person the MOST SPECIFIC wins (nationality, then grade, then contract type), then the higher priority."""

    class Mode(models.TextChoices):
        COMPANY = "company", "Company books the ticket"
        ALLOWANCE = "allowance", "Allowance paid through payroll"

    company = models.ForeignKey("organization.Company", on_delete=models.PROTECT, related_name="+")
    name = models.CharField(max_length=120)
    nationalities = models.JSONField(default=list, blank=True)
    min_grade = models.ForeignKey("organization.Grade", null=True, blank=True, on_delete=models.PROTECT,
                                  related_name="+", help_text="This grade and above.")
    employment_types = models.JSONField(default=list, blank=True)
    interval_months = models.PositiveSmallIntegerField(
        "Every (months)", default=12, validators=[MinValueValidator(1), MaxValueValidator(60)])
    first_after_months = models.PositiveSmallIntegerField(
        "First ticket after (months of service)", default=12, validators=[MaxValueValidator(60)])
    spouse_covered = models.BooleanField(default=False)
    max_children = models.PositiveSmallIntegerField(default=0, validators=[MaxValueValidator(10)])
    mode = models.CharField(max_length=10, choices=Mode.choices, default=Mode.COMPANY)
    allowance_amount = models.DecimalField("Allowance per traveller (BHD)", max_digits=12, decimal_places=3,
                                           null=True, blank=True, validators=[MinValueValidator(Decimal("0"))])
    priority = models.SmallIntegerField(default=0, help_text="Breaks a tie between equally specific policies.")
    is_active = models.BooleanField(default=True)

    objects = CompanyQuerySet.as_manager()

    class Meta:
        ordering = ["company", "name"]
        constraints = [models.UniqueConstraint(fields=["company", "name"], name="uniq_ticket_policy_name")]

    @property
    def specificity(self):
        return 4 * bool(self.nationalities) + 2 * bool(self.min_grade_id) + bool(self.employment_types)

    @property
    def applies_to_text(self):
        parts = [", ".join(self.nationalities) if self.nationalities else "All nationalities",
                 f"{self.min_grade.name} and above" if self.min_grade_id else "any grade"]
        if self.employment_types:
            parts.append(", ".join(t.capitalize() for t in self.employment_types))
        return " · ".join(parts)

    @property
    def covers_text(self):
        who = ["the employee"]
        if self.spouse_covered:
            who.append("spouse")
        if self.max_children:
            who.append(f"up to {self.max_children} child{'ren' if self.max_children != 1 else ''}")
        return ", ".join(who)

    def mismatches(self, employee):
        """Why this policy does not fit the employee: an empty list means it fits."""
        reasons = []
        if self.nationalities and nationality_key(employee.nationality) not in {nationality_key(n) for n in self.nationalities}:
            reasons.append(f"nationality ({employee.nationality or 'not set'}) is not covered")
        if self.min_grade_id:
            if employee.grade_id is None:
                reasons.append("no grade is set")
            elif employee.grade.rank < self.min_grade.rank:
                reasons.append(f"grade {employee.grade.name} is below {self.min_grade.name}")
        if self.employment_types and employee.employment_type not in self.employment_types:
            reasons.append(f"contract type ({employee.get_employment_type_display().lower()}) is not covered")
        return reasons

    def __str__(self):
        return self.name


@audited(module="airtickets", company="company_id", subject="employee_id")
class TicketEntitlement(BaseModel):
    """One ticket an employee is entitled to. There is always at most one OPEN row per employee, "the next ticket";
    when it is taken (or paid, or waived) it is closed and the next one is worked out from it. The terms are copied
    from the policy and kept up to date while the row is open; once closed they are history and never change."""

    class Status(models.TextChoices):
        OPEN = "open", "Next ticket"
        USED = "used", "Taken"
        PAID = "paid", "Paid as allowance"
        LAPSED = "lapsed", "Waived"

    company = models.ForeignKey("organization.Company", on_delete=models.PROTECT, related_name="+")
    employee = models.ForeignKey("employees.Employee", on_delete=models.PROTECT, related_name="ticket_entitlements")
    policy = models.ForeignKey(TicketPolicy, null=True, blank=True, on_delete=models.SET_NULL,
                               related_name="entitlements")
    cycle = models.PositiveIntegerField()
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.OPEN)
    eligible_from = models.DateField()
    mode = models.CharField(max_length=10, choices=TicketPolicy.Mode.choices)
    allowance_amount = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    spouse_covered = models.BooleanField(default=False)
    max_children = models.PositiveSmallIntegerField(default=0)
    # what happened, once it is closed
    used_on = models.DateField(null=True, blank=True, help_text="Travel date, or the date the allowance was paid.")
    destination = models.CharField(max_length=80, blank=True)
    cost = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    notes = models.TextField(blank=True)

    objects = CompanyQuerySet.as_manager()

    class Meta:
        ordering = ["employee", "-cycle"]
        constraints = [
            models.UniqueConstraint(fields=["employee", "cycle"], name="uniq_ticket_cycle"),
            models.UniqueConstraint(fields=["employee"], condition=models.Q(status="open"),
                                    name="one_open_ticket_per_employee"),
        ]

    def save(self, *args, **kwargs):
        self.company_id = self.employee.company_id
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.employee} ticket {self.cycle} ({self.get_status_display()})"


@audited(module="airtickets", company="company_id", subject="employee_id")
class TravelProfile(BaseModel):
    """Where an employee travels home to."""
    company = models.ForeignKey("organization.Company", on_delete=models.PROTECT, related_name="+")
    employee = models.OneToOneField("employees.Employee", on_delete=models.CASCADE, related_name="travel_profile")
    home_country = models.CharField(max_length=60, blank=True)
    destination_airport = models.CharField(max_length=80, blank=True, help_text="For example: Cochin (COK).")
    notes = models.TextField(blank=True)

    objects = CompanyQuerySet.as_manager()

    def save(self, *args, **kwargs):
        self.company_id = self.employee.company_id
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.employee}: {self.destination_airport or self.home_country or 'not set'}"
