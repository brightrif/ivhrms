from django.apps import AppConfig
from django.db.models.signals import post_migrate


class DependantsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.dependants"
    label = "dependants"
    verbose_name = "Family & Visas"

    def ready(self):
        from . import signals  # noqa: F401  (reacts to approval outcomes)
        from .defaults import ensure_system_defaults
        # Group permissions and the visa approval flow exist after every `migrate`: no seed command to remember.
        post_migrate.connect(ensure_system_defaults, sender=self, dispatch_uid="dependants.ensure_system_defaults")