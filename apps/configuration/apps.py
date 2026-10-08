from django.apps import AppConfig
from django.db.models.signals import post_migrate
from django.utils.module_loading import autodiscover_modules


class ConfigurationConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.configuration"
    verbose_name = "Settings"

    def ready(self):
        # Every installed app may describe its own settings in a settings_spec.py. They are read here once, so a
        # new module gets its Settings tab without anyone editing this app.
        autodiscover_modules("settings_spec")
        from .defaults import ensure_system_defaults
        post_migrate.connect(ensure_system_defaults, sender=self, dispatch_uid="configuration.ensure_system_defaults")
