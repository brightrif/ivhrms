from django.dispatch import Signal, receiver
from django.utils import timezone

from apps.core.signals import approval_completed
from apps.employees.signals import employee_onboarded

leave_approved = Signal()    # kwargs: leave_request  (sent inside the approval's transaction)
leave_cancelled = Signal()


@receiver(approval_completed)
def on_approval_completed(sender, approval, **kwargs):
    from django.contrib.contenttypes.models import ContentType
    from . import services
    from .models import LeaveRequest
    if approval.content_type_id == ContentType.objects.get_for_model(LeaveRequest).id:
        services.finalize(approval)


@receiver(employee_onboarded)
def grant_initial_entitlements(sender, employee, **kwargs):
    from . import services
    from .models import LeaveType
    year = max(employee.joining_date.year, timezone.localdate().year)
    types = LeaveType.objects.filter(company=employee.company, is_active=True,
                                     tracks_balance=True, annual_entitlement__gt=0)
    for leave_type in types:
        services.grant_entitlement(employee, leave_type, year)