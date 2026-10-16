from django.conf import settings
from django.db import models
from apps.core.models import BaseModel
from apps.audit.registry import audited

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


@audited(module="organization")
class Designation(BaseModel):
    name = models.CharField(max_length=120, unique=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class Grade(BaseModel):
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=80)
    rank = models.PositiveSmallIntegerField(default=0, help_text="Higher = more senior")

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
    position = models.PositiveSmallIntegerField(default=100, help_text="Lower comes first in the list.")

    class Meta:
        ordering = ["position", "name"]
        verbose_name_plural = "countries"

    def __str__(self):
        return f"{self.name} (+{self.dial_code})"

