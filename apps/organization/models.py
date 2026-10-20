from django.conf import settings
from django.db import models
from apps.core.models import BaseModel
from apps.audit.registry import audited
from .scope import EMBLEMS
from .company_lists import CompanyListManager, check_unique, claim_current_company

@audited(module="organization", company="id")
class Company(BaseModel):
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=150)
    name_ar = models.CharField("Name (Arabic)", max_length=150, blank=True)
    cr_number = models.CharField("Commercial registration no.", max_length=40, blank=True)
    sio_number = models.CharField("SIO employer no.", max_length=40, blank=True)
    address = models.TextField(blank=True)
    currency = models.CharField(max_length=3, default="BHD")
    is_active = models.BooleanField(default=True)
    runs_projects = models.BooleanField(default=False, help_text="Offered when adding a project.")
    is_default = models.BooleanField(default=False, help_text="Chosen first on pages that ask for a company.")
    is_default_labor = models.BooleanField(default=False, help_text="Chosen first when adding labor workers.")
    theme_color = models.CharField("Theme colour", max_length=7, blank=True,
                                   help_text="Buttons and links in the Separate view, e.g. #1a5fa8. Empty = standard.")
    sidebar_color = models.CharField("Sidebar colour", max_length=7, blank=True,
                                     help_text="Sidebar in the Separate view. Empty = standard.")
    emblem = models.CharField(max_length=10, blank=True, choices=EMBLEMS,
                              help_text="Small animated picture under the IV logo in the Separate view.")

    class Meta:
        verbose_name_plural = "companies"

    def __str__(self):
        return self.name

class CompanyAccess(models.Model):
    """Which companies a user may work in. Employees' own data is reached via their Employee link."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="company_access")
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="access")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "company"], name="uniq_user_company")]

@audited(module="organization", company="company_id")
class Department(BaseModel):
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="departments")
    code = models.CharField(max_length=20)
    name = models.CharField(max_length=120)
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.RESTRICT, related_name="children")
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["company", "code"], name="uniq_dept_code_per_company")]

    def __str__(self):
        return self.name


@audited(module="organization", company="company_id")
class Designation(BaseModel):
    """A job title. With no company it is shared by every company; with a company it belongs to that company only."""
    company = models.ForeignKey(Company, null=True, blank=True, on_delete=models.CASCADE, related_name="+",
                                help_text="Empty = shared by every company")
    name = models.CharField(max_length=120)
    is_active = models.BooleanField(default=True)
    family_visa_default = models.BooleanField(
        "Family visa allowed by default", default=False,
        help_text="Everyone with this designation may have family visas, unless HR or Management say "
                  "otherwise on their page. Tick the manager-level designations.")

    objects = CompanyListManager()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["company", "name"], name="uniq_designation_name_per_company"),
            models.UniqueConstraint(fields=["name"], condition=models.Q(company__isnull=True),
                                    name="uniq_designation_shared_name"),
        ]

    def save(self, *args, **kwargs):
        claim_current_company(self)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class Grade(BaseModel):
    """A pay or seniority grade. With no company it is shared by every company; with a company it is that company's."""
    company = models.ForeignKey(Company, null=True, blank=True, on_delete=models.CASCADE, related_name="+",
                                help_text="Empty = shared by every company")
    code = models.CharField(max_length=20)
    name = models.CharField(max_length=80)
    rank = models.PositiveSmallIntegerField(default=0, help_text="Higher = more senior")

    objects = CompanyListManager()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["company", "code"], name="uniq_grade_code_per_company"),
            models.UniqueConstraint(fields=["code"], condition=models.Q(company__isnull=True),
                                    name="uniq_grade_shared_code"),
        ]

    def clean(self):
        check_unique(self, "code", "grade code")

    def save(self, *args, **kwargs):
        claim_current_company(self)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class Location(BaseModel):
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=120)
    is_site = models.BooleanField(default=False, help_text="Work site / camp, not an office")
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class Project(BaseModel):
    class Status(models.TextChoices):
        ACTIVE = "active"
        ON_HOLD = "on_hold"
        CLOSED = "closed"

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="projects")
    code = models.CharField(max_length=30)
    name = models.CharField(max_length=150)
    location = models.ForeignKey("Location", null=True, blank=True, on_delete=models.PROTECT)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["company", "code"], name="uniq_project_code_per_company")]

    def __str__(self):
        return f"{self.code} {self.name}"


@audited(module="organization")
class Country(BaseModel):
    """A country whose phone code can be picked on phone boxes. Settings > Organization switches them on and off."""
    name = models.CharField(max_length=80, unique=True)
    dial_code = models.CharField(max_length=5, unique=True, help_text="Digits only, without the +, e.g. 973.")
    iso = models.CharField("Flag", max_length=2, blank=True, help_text="Two letters of the flag, e.g. BH.")
    is_active = models.BooleanField(default=False)
    is_nationality = models.BooleanField(default=False, help_text="Offered in the Nationality list.")
    position = models.PositiveSmallIntegerField(default=100, help_text="Lower comes first in the list.")

    class Meta:
        ordering = ["position", "name"]
        verbose_name_plural = "countries"

    def __str__(self):
        return f"{self.name} (+{self.dial_code})"

