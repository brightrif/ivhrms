from django.dispatch import receiver

from apps.employees.signals import employee_onboarded


@receiver(employee_onboarded)
def first_entitlement(sender, employee, **kwargs):
    from . import services
    services.sync_entitlement(employee)
