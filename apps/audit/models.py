from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models
from django.utils import timezone


class AppendOnlyQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise PermissionError("Audit events are append-only.")

    def delete(self):
        raise PermissionError("Audit events are append-only.")

    def bulk_update(self, *args, **kwargs):
        raise PermissionError("Audit events are append-only.")


class AuditEvent(models.Model):
    class Action(models.TextChoices):
        CREATE = "create"
        UPDATE = "update"
        DELETE = "delete"
        VIEW = "view"
        DOWNLOAD = "download"
        EXPORT = "export"
        LOGIN = "login"
        LOGIN_FAILED = "login_failed"
        LOGOUT = "logout"
        APPROVE = "approve"
        REJECT = "reject"
        DENIED = "permission_denied"

    class Channel(models.TextChoices):
        WEB = "web"
        WHATSAPP = "whatsapp"
        SYSTEM = "system"

    timestamp = models.DateTimeField(default=timezone.now, db_index=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.DO_NOTHING, db_constraint=False, related_name="+",
    )
    actor_repr = models.CharField(max_length=150, blank=True)
    action = models.CharField(max_length=20, choices=Action.choices)
    module = models.CharField(max_length=50, blank=True)


    content_type = models.ForeignKey(
        ContentType, null=True, blank=True,
        on_delete=models.DO_NOTHING, db_constraint=False, related_name="+",
    )
    object_id = models.CharField(max_length=64, blank=True)
    object_repr = models.CharField(max_length=255, blank=True)

    # Plain integer, not a FK: audit depends on nothing and survives deletions.
    subject_employee_id = models.BigIntegerField(null=True, blank=True)
    subject_repr = models.CharField(max_length=255, blank=True)

    company_id = models.BigIntegerField(null=True, blank=True, db_index=True)

    changes = models.JSONField(default=dict, blank=True, encoder=DjangoJSONEncoder)
    channel = models.CharField(max_length=10, choices=Channel.choices, default=Channel.SYSTEM)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=255, blank=True)
    request_id = models.UUIDField(null=True, blank=True)
    reason = models.TextField(blank=True)

    objects = AppendOnlyQuerySet.as_manager()

    class Meta:
        ordering = ["-timestamp", "-id"]
        indexes = [
            models.Index(fields=["content_type", "object_id"]),
            models.Index(fields=["subject_employee_id", "-timestamp"]),
            models.Index(fields=["actor", "-timestamp"]),
            models.Index(fields=["module", "-timestamp"]),
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise PermissionError("Audit events are append-only.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("Audit events are append-only.")

    def __str__(self):
        return f"{self.timestamp:%Y-%m-%d %H:%M} {self.actor_repr or 'system'} {self.action} {self.object_repr}"