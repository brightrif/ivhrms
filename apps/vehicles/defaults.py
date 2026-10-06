"""System data created automatically after every `migrate`. Nothing here needs a seed command.

Permissions are given to the standard groups only the first time, so anything changed later in the
admin survives future migrations.
"""
from django.db import DEFAULT_DB_ALIAS, connections

_VIEW = ["view_vehicle"]
GROUP_PERMISSIONS = {
    "HR": _VIEW + ["add_vehicle", "change_vehicle"],
    "Finance": _VIEW,
    "Management": _VIEW,
}


def ensure_system_defaults(sender=None, using=DEFAULT_DB_ALIAS, **kwargs):
    if "vehicles_vehicle" not in connections[using].introspection.table_names():
        return
    from django.contrib.auth.models import Group, Permission

    for group_name, codenames in GROUP_PERMISSIONS.items():
        group, _ = Group.objects.using(using).get_or_create(name=group_name)
        if group.permissions.filter(content_type__app_label="vehicles").exists():
            continue
        perms = Permission.objects.using(using).filter(content_type__app_label="vehicles", codename__in=codenames)
        group.permissions.add(*perms)
