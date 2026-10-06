from django.contrib.contenttypes.models import ContentType

from .context import get_overrides, get_request
from .models import AuditEvent


def log(action, obj=None, *, module="", subject_employee_id=None, subject_repr="",
        company_id=None, changes=None, reason="", channel=None, actor=None, object_repr=None):
    request = get_request()
    overrides = get_overrides()

    if actor is None:
        actor = overrides.get("actor")
    if actor is None and request is not None:
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            actor = user

    if channel is None:
        channel = overrides.get("channel") or ("web" if request else "system")

    meta = request.META if request is not None else {}
    return AuditEvent.objects.create(
        actor=actor,
        actor_repr=str(actor) if actor else "",
        action=action,
        module=module or (obj._meta.app_label if obj is not None else ""),
        content_type=ContentType.objects.get_for_model(obj) if obj is not None else None,
        object_id=str(obj.pk) if obj is not None and obj.pk is not None else "",
        object_repr=(object_repr or (str(obj) if obj is not None else ""))[:255],
        subject_employee_id=subject_employee_id,
        subject_repr=subject_repr[:255],
        company_id=company_id,
        changes=changes or {},
        channel=channel,
        ip_address=meta.get("REMOTE_ADDR") or None,   # revisit behind a proxy
        user_agent=meta.get("HTTP_USER_AGENT", "")[:255],
        request_id=getattr(request, "audit_request_id", None),
        reason=reason,
    )