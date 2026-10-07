from django.apps import AppConfig
from django.db.models.signals import post_migrate


class LaborConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.labor"
    verbose_name = "Labor"

    def ready(self):
        from .defaults import ensure_system_defaults
        # Group permissions exist after every `migrate`, so there is no seed command to remember.
        post_migrate.connect(ensure_system_defaults, sender=self, dispatch_uid="labor.ensure_system_defaults")
