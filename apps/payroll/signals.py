from django.dispatch import receiver

from apps.core.signals import approval_completed


@receiver(approval_completed)
def payroll_decided(sender, approval, **kwargs):
    from . import runs, settlements
    if approval.flow.code == runs.FLOW_CODE:
        runs.finalize(approval)
    elif approval.flow.code == settlements.FLOW_CODE:
        settlements.finalize(approval)
