from django.conf import settings
from django.db import models, transaction
from django.core.validators import MaxValueValidator, MinValueValidator

from apps.audit.registry import audited
from apps.core.models import BaseModel

from apps.core.scoping import CompanyQuerySet
from django.core.exceptions import ValidationError





@audited(module="employees", subject="pk", company="company_id")
class Employee(BaseModel):
    class WorkerType(models.TextChoices):
        STAFF = "staff"
        LABOR = "labor"

    class EmploymentType(models.TextChoices):
        PERMANENT = "permanent"
        TEMPORARY = "temporary"
        CONTRACT = "contract"

    class Status(models.TextChoices):
        ACTIVE = "active"
        ON_NOTICE = "on_notice"
        SEPARATED = "separated"

    user = models.OneToOneField(settings.AUTH_USER_MODEL, null=True, blank=True,
                                on_delete=models.SET_NULL, related_name="employee")
    company = models.ForeignKey("organization.Company", on_delete=models.PROTECT, related_name="employees")
    # Employee number is assigned automatically if left blank. It is unique per company.
    employee_no = models.CharField(max_length=30, blank=True,
                                   help_text="Assigned automatically when left blank.")
    worker_type = models.CharField(max_length=10, choices=WorkerType.choices, default=WorkerType.STAFF)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)

    first_name = models.CharField(max_length=80)
    last_name = models.CharField(max_length=80, blank=True)
    name_ar = models.CharField("Name (Arabic)", max_length=160, blank=True)
    date_of_birth = models.DateField(null=True, blank=True)
    nationality = models.CharField(max_length=60, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=30, blank=True)
    whatsapp_number = models.CharField(max_length=20, null=True, blank=True, unique=True)
    whatsapp_verified = models.BooleanField(default=False)

    # Current assignment. Maintained from EmploymentRecord via services.record_assignment().
    employment_type = models.CharField(max_length=10, choices=EmploymentType.choices,
                                       default=EmploymentType.PERMANENT)
    department = models.ForeignKey("organization.Department", null=True, blank=True,
                                   on_delete=models.PROTECT, related_name="+")
    designation = models.ForeignKey("organization.Designation", null=True, blank=True,
                                    on_delete=models.PROTECT, related_name="+")
    grade = models.ForeignKey("organization.Grade", null=True, blank=True,
                              on_delete=models.PROTECT, related_name="+")
    location = models.ForeignKey("organization.Location", null=True, blank=True,
                                 on_delete=models.PROTECT, related_name="+")
    reporting_manager = models.ForeignKey("self", null=True, blank=True,
                                          on_delete=models.SET_NULL, related_name="direct_reports")
    joining_date = models.DateField()

    objects = CompanyQuerySet.as_manager()

    class Meta:
        ordering = ["employee_no"]

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}".strip()

    def save(self, *args, **kwargs):
        self.whatsapp_number = self.whatsapp_number or None   # "" would break the unique constraint
        if self._state.adding and not self.employee_no:
            from . import numbering
            with transaction.atomic():      # the number and the row succeed or fail together
                self.employee_no = numbering.next_number(self.company, self.worker_type)
                super().save(*args, **kwargs)
            return
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.employee_no} - {self.full_name}"

    class Meta:
        ordering = ["company", "employee_no"]
        permissions = [("manage_logins", "Can create and reset employee logins")]
        constraints = [models.UniqueConstraint(fields=["company", "employee_no"],
                                            name="uniq_employee_no_per_company")]

    def clean(self):
        for name in ("reporting_manager", "department"):
            obj = getattr(self, name)
            if obj is not None and obj.company_id != self.company_id:
                raise ValidationError({name: "Must belong to the same company as the employee."})
            
@audited(module="employees", subject="employee_id", company="company_id")
class EmploymentRecord(BaseModel):
    """Effective-dated history. Payroll, attendance and reports ask 'what was true on date X'."""
    class Reason(models.TextChoices):
        JOINING = "joining"
        TRANSFER = "transfer"
        PROMOTION = "promotion"
        MANAGER_CHANGE = "manager_change"
        CONTRACT_CHANGE = "contract_change"
        CORRECTION = "correction"

    company = models.ForeignKey("organization.Company", on_delete=models.PROTECT, related_name="+")
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="history")
    effective_from = models.DateField()
    effective_to = models.DateField(null=True, blank=True)     # null = still current
    employment_type = models.CharField(max_length=10, choices=Employee.EmploymentType.choices)
    department = models.ForeignKey("organization.Department", null=True, on_delete=models.PROTECT, related_name="+")
    designation = models.ForeignKey("organization.Designation", null=True, on_delete=models.PROTECT, related_name="+")
    grade = models.ForeignKey("organization.Grade", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    location = models.ForeignKey("organization.Location", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    reporting_manager = models.ForeignKey(Employee, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    reason = models.CharField(max_length=20, choices=Reason.choices)
    remarks = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-effective_from"]
        constraints = [
            models.UniqueConstraint(fields=["employee", "effective_from"], name="uniq_employee_effective_from"),
            models.UniqueConstraint(fields=["employee"], condition=models.Q(effective_to__isnull=True),
                                    name="one_open_record_per_employee"),
        ]

@audited(module="employees", company="company_id")
class EmployeeNumberSequence(BaseModel):
    """Counter behind automatic employee numbers: one per company and worker type, created on first use."""
    company = models.ForeignKey("organization.Company", on_delete=models.CASCADE, related_name="+")
    worker_type = models.CharField(max_length=10, choices=Employee.WorkerType.choices)
    prefix = models.CharField(max_length=12, blank=True)
    padding = models.PositiveSmallIntegerField(default=4, validators=[MinValueValidator(1), MaxValueValidator(8)])
    next_number = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])

    class Meta:
        constraints = [models.UniqueConstraint(fields=["company", "worker_type"], name="uniq_number_sequence")]

    def __str__(self):
        return f"{self.company.code} {self.worker_type}: next {self.prefix}{self.next_number:0{self.padding}d}"