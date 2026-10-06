from django.apps import AppConfig
from django.db.models.signals import post_migrate


class ComplianceConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.compliance"

    def ready(self):
        from .defaults import ensure_system_defaults
        # Document types and groups exist after every `migrate`, so there is no seed command to remember.
        post_migrate.connect(ensure_system_defaults, sender=self,
                             dispatch_uid="compliance.ensure_system_defaults")
