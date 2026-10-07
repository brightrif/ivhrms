from django.dispatch import receiver

from apps.core.signals import approval_completed


@receiver(approval_completed)
def custody_decided(sender, approval, **kwargs):
    from . import handovers
    if approval.flow.code == handovers.FLOW_CODE:
        handovers.finalize(approval)
