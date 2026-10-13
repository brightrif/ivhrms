from django.dispatch import receiver

from apps.core.signals import approval_completed


@receiver(approval_completed)
def visa_decided(sender, approval, **kwargs):
    from . import visa_services
    if approval.flow.code == visa_services.FLOW_CODE:
        visa_services.finalize(approval)
