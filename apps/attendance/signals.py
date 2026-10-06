from django.contrib.contenttypes.models import ContentType
from django.dispatch import receiver

from apps.core.signals import approval_completed
from apps.leave.signals import leave_approved, leave_cancelled


@receiver(approval_completed)
def on_approval_completed(sender, approval, **kwargs):
    from . import services
    from .models import AttendanceCorrection
    if approval.content_type_id == ContentType.objects.get_for_model(AttendanceCorrection).id:
        services.finalize_correction(approval)


@receiver(leave_approved)
def on_leave_approved(sender, leave_request, **kwargs):
    from . import services
    services.apply_leave(leave_request)


@receiver(leave_cancelled)
def on_leave_cancelled(sender, leave_request, **kwargs):
    from . import services
    services.remove_leave(leave_request)