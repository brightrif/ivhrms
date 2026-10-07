from django.apps import AppConfig
from django.db.models.signals import post_migrate


class VehiclesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.vehicles"
    verbose_name = "Vehicles"

    def ready(self):
        from . import signals  # noqa: F401  (reacts to approval outcomes)
        from .defaults import ensure_system_defaults
        post_migrate.connect(ensure_system_defaults, sender=self)
