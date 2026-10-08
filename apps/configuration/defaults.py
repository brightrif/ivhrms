"""System data created automatically after every `migrate`. Nothing here needs a seed command.

Each registered module gets two rights: see its settings and change them. Nobody is given them here: the superuser
decides who, on the Access tab. A superuser can always do everything."""
from django.db import DEFAULT_DB_ALIAS, connections

from . import registry


def ensure_system_defaults(sender=None, using=DEFAULT_DB_ALIAS, **kwargs):
    if "configuration_settingvalue" not in set(connections[using].introspection.table_names()):
        return
    from django.contrib.auth.models import Permission
    from django.contrib.contenttypes.models import ContentType

    from .models import SettingValue

    content_type = ContentType.objects.db_manager(using).get_for_model(SettingValue)
    for module in registry.modules():
        for verb, text in (("view", "See"), ("edit", "Change")):
            Permission.objects.using(using).get_or_create(
                content_type=content_type, codename=f"{verb}_settings_{module.key}",
                defaults={"name": f"{text} {module.label} settings"})
