from django.dispatch import receiver

from apps.employees.signals import employee_onboarded


@receiver(employee_onboarded)
def assign_initial_shift(sender, employee, shift=None, **kwargs):
    if shift is not None:
        from . import services
        services.assign_shift(employee, shift, employee.joining_date)