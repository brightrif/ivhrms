from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from apps.audit.registry import audited
from apps.compliance.storage import private_storage
from apps.compliance.validators import validate_extension, validate_file_size
from apps.core.models import BaseModel
from apps.core.scoping import CompanyQuerySet

from .storage import attachment_path


@audited(module="dependants", company="company_id", subject="employee_id")
class Dependant(BaseModel):
    """A family member of an employee (spouse, child, parent).

    Passport, CPR and visa are NOT stored here: they are compliance Documents of a "dependant" type, so
    expiry alerts, renewal tasks and payments work exactly as they do for employees. `company` always
    follows the employee, like it does for compliance documents."""

    class Relationship(models.TextChoices):
        SPOUSE = "spouse", "Spouse"
        CHILD = "child", "Child"
        PARENT = "parent", "Parent"

    class Gender(models.TextChoices):
        MALE = "male", "Male"
        FEMALE = "female", "Female"

    company = models.ForeignKey("organization.Company", on_delete=models.PROTECT, related_name="+")
    employee = models.ForeignKey("employees.Employee", on_delete=models.PROTECT, related_name="dependants")
    relationship = models.CharField(max_length=10, choices=Relationship.choices)
    first_name = models.CharField(max_length=80)
    last_name = models.CharField(max_length=80, blank=True)
    name_ar = models.CharField("Name (Arabic)", max_length=160, blank=True)
    gender = models.CharField(max_length=6, choices=Gender.choices, blank=True)
    date_of_birth = models.DateField(null=True, blank=True)
    nationality = models.CharField(max_length=60, blank=True)
    is_active = models.BooleanField(default=True, help_text="Untick when the person is no longer sponsored.")
    left_on = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)

    objects = CompanyQuerySet.as_manager()

    class Meta:
        ordering = ["employee__employee_no", "relationship", "date_of_birth", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["employee", "relationship", "first_name", "last_name", "date_of_birth"],
                name="uniq_dependant_per_employee",
                violation_error_message="This family member is already recorded for this employee."),
        ]
        indexes = [models.Index(fields=["company", "is_active"])]

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def age(self):
        d = self.date_of_birth
        if not d:
            return None
        today = timezone.localdate()
        return today.year - d.year - ((today.month, today.day) < (d.month, d.day))

    def save(self, *args, **kwargs):
        self.company_id = self.employee.company_id            # the company always follows the employee
        self.first_name = " ".join(self.first_name.split())
        self.last_name = " ".join(self.last_name.split())
        super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        self.first_name = " ".join(self.first_name.split())     # so the duplicate check sees what will be saved
        self.last_name = " ".join(self.last_name.split())
        errors = {}
        if self.date_of_birth and self.date_of_birth > timezone.localdate():
            errors["date_of_birth"] = "The date of birth cannot be in the future."
        if self.relationship == self.Relationship.CHILD and not self.date_of_birth:
            errors["date_of_birth"] = "A child's date of birth is required."
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"{self.full_name} ({self.get_relationship_display()} of {self.employee.full_name})"


@audited(module="dependants", company="company_id", subject="employee_id")
class DependantAttachment(BaseModel):
    """Supporting papers that never expire (marriage / birth certificate, medical report...).

    They are kept here and not as compliance Documents because a Document must have an expiry date."""

    class Kind(models.TextChoices):
        MARRIAGE_CERTIFICATE = "marriage_certificate", "Marriage certificate"
        BIRTH_CERTIFICATE = "birth_certificate", "Birth certificate"
        MEDICAL_REPORT = "medical_report", "Medical report"
        PHOTO = "photo", "Photograph"
        OTHER = "other", "Other"

    company = models.ForeignKey("organization.Company", on_delete=models.PROTECT, related_name="+")
    employee = models.ForeignKey("employees.Employee", on_delete=models.PROTECT, related_name="+")
    dependant = models.ForeignKey(Dependant, on_delete=models.PROTECT, related_name="attachments")
    kind = models.CharField(max_length=24, choices=Kind.choices)
    description = models.CharField(max_length=120, blank=True)
    file = models.FileField(storage=private_storage, upload_to=attachment_path, max_length=200,
                            validators=[validate_extension, validate_file_size])
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name="+")

    objects = CompanyQuerySet.as_manager()

    class Meta:
        ordering = ["dependant", "kind", "-id"]

    def save(self, *args, **kwargs):
        self.employee_id = self.dependant.employee_id
        self.company_id = self.dependant.company_id
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.get_kind_display()} - {self.dependant.full_name}"
