from django.apps import AppConfig
from django.db.models.signals import post_migrate


class AirticketsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.airtickets"
    label = "airtickets"
    verbose_name = "Air tickets"

    def ready(self):
        from . import signals  # noqa: F401  (a new employee gets their first entitlement)
        from .defaults import ensure_system_defaults
        post_migrate.connect(ensure_system_defaults, sender=self, dispatch_uid="airtickets.ensure_system_defaults")
