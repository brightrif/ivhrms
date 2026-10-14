from django.apps import AppConfig
from django.db.models.signals import post_migrate


class PayrollConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.payroll"
    verbose_name = "Payroll"

    def ready(self):
        from . import signals  # noqa: F401  (connects the approval receiver)
        from apps.labor import locks as labor_locks

        from .locks import labor_period_closed
        labor_locks.register(labor_period_closed)             # an approved labor run closes its month for labor
        from .defaults import ensure_system_defaults
        post_migrate.connect(ensure_system_defaults, sender=self, dispatch_uid="payroll.ensure_system_defaults")
