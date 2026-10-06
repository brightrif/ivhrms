from django.conf import settings
from django.db import models
from apps.audit.context import current_user

from apps.audit.registry import audited  # noqa: E402
from django.contrib.contenttypes.fields import GenericForeignKey  # noqa: E402
from django.contrib.contenttypes.models import ContentType  # noqa: E402

class BaseModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, editable=False,
                                   on_delete=models.SET_NULL, related_name="+")
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, editable=False,
                                   on_delete=models.SET_NULL, related_name="+")

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        user = current_user()
        if user is not None:
            if self._state.adding and self.created_by_id is None:
                self.created_by = user
            self.updated_by = user
        if kwargs.get("update_fields") is not None:
            kwargs["update_fields"] = set(kwargs["update_fields"]) | {"updated_at", "updated_by"}
        super().save(*args, **kwargs)


class SystemSetting(BaseModel):
    key = models.CharField(max_length=100, unique=True)
    value = models.JSONField()
    description = models.CharField(max_length=255, blank=True)

    def __str__(self):
        return self.key


class ApprovalFlow(BaseModel):
    company = models.ForeignKey("organization.Company", null=True, blank=True,
                                on_delete=models.CASCADE, related_name="+",
                                help_text="Empty = default flow for all companies")
    code = models.CharField(max_length=60)                  # remove unique=True
    name = models.CharField(max_length=120)
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["company", "code"], name="uniq_flow_company_code"),
            models.UniqueConstraint(fields=["code"], condition=models.Q(company__isnull=True),
                                    name="uniq_default_flow_code"),
        ]
    def __str__(self):
        return f"{self.name} ({self.company.code if self.company_id else 'default'})"


class ApprovalStep(models.Model):
    class ApproverType(models.TextChoices):
        REPORTING_MANAGER = "manager", "Employee's reporting manager"
        GROUP = "group", "Anyone in a role/group"
        USER = "user", "A specific user"

    flow = models.ForeignKey(ApprovalFlow, on_delete=models.CASCADE, related_name="steps")
    order = models.PositiveSmallIntegerField()
    name = models.CharField(max_length=100)
    approver_type = models.CharField(max_length=10, choices=ApproverType.choices)
    group = models.ForeignKey("auth.Group", null=True, blank=True, on_delete=models.PROTECT)
    user = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.PROTECT,
                             related_name="+")

    class Meta:
        ordering = ["order"]
        constraints = [models.UniqueConstraint(fields=["flow", "order"], name="uniq_flow_step_order")]

    def __str__(self):
        return f"{self.flow.code} #{self.order} {self.name}"





@audited(module="approvals", subject="employee_id", company="company_id")
class ApprovalRequest(BaseModel):
    class Status(models.TextChoices):
        PENDING = "pending"
        APPROVED = "approved"
        REJECTED = "rejected"
        CANCELLED = "cancelled"

    flow = models.ForeignKey(ApprovalFlow, on_delete=models.PROTECT, related_name="requests")
    company = models.ForeignKey("organization.Company", on_delete=models.PROTECT, related_name="+")
    content_type = models.ForeignKey(ContentType, on_delete=models.PROTECT, related_name="+")
    object_id = models.CharField(max_length=64)
    target = GenericForeignKey("content_type", "object_id")
    employee = models.ForeignKey("employees.Employee", on_delete=models.PROTECT, related_name="+")
    requested_by = models.ForeignKey("accounts.User", on_delete=models.PROTECT, related_name="+")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    current_step = models.PositiveSmallIntegerField(null=True, blank=True)  # ApprovalStep.order
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["status", "current_step"]),
            models.Index(fields=["content_type", "object_id"]),
        ]


class ApprovalAction(models.Model):
    """One decision on one step. Never edited after creation."""
    class Decision(models.TextChoices):
        APPROVE = "approve"
        REJECT = "reject"

    request = models.ForeignKey(ApprovalRequest, on_delete=models.CASCADE, related_name="actions")
    step = models.ForeignKey(ApprovalStep, on_delete=models.PROTECT, related_name="+")
    actor = models.ForeignKey("accounts.User", on_delete=models.PROTECT, related_name="+")
    decision = models.CharField(max_length=10, choices=Decision.choices)
    comment = models.TextField(blank=True)
    channel = models.CharField(max_length=10, default="web")
    acted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["acted_at"]