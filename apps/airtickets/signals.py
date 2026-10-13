from django.dispatch import receiver

from apps.core.signals import approval_completed
from apps.employees.signals import employee_onboarded


@receiver(employee_onboarded)
def first_entitlement(sender, employee, **kwargs):
    from . import services
    services.sync_entitlement(employee)


@receiver(approval_completed)
def ticket_request_decided(sender, approval, **kwargs):
    from . import request_services
    from .defaults import FLOW_CODE
    if approval.flow.code == FLOW_CODE:
        request_services.finalize(approval)
