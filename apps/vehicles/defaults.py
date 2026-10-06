"""System data created automatically after every `migrate`. Nothing here needs a seed command.

Permissions are given to the standard groups once per model, the first time that model's permissions are
missing from the group, so anything changed later in the admin survives future migrations (and a model
added in a later phase still gets its permissions).
"""
from django.db import DEFAULT_DB_ALIAS, connections

_READ = {"vehicle": ["view"], "vehicleassignment": ["view"], "odometerreading": ["view"]}
GROUP_PERMISSIONS = {
    "HR": {"vehicle": ["view", "add", "change"], "vehicleassignment": ["view", "add", "change"],
           "odometerreading": ["view", "add"]},
    "Finance": _READ,
    "Management": _READ,
}


def ensure_system_defaults(sender=None, using=DEFAULT_DB_ALIAS, **kwargs):
    tables = set(connections[using].introspection.table_names())
    if "vehicles_vehicle" not in tables:
        return
    from django.contrib.auth.models import Group, Permission

    for group_name, models_ in GROUP_PERMISSIONS.items():
        group, _ = Group.objects.using(using).get_or_create(name=group_name)
        for model, actions in models_.items():
            if f"vehicles_{model}" not in tables:
                continue
            if group.permissions.filter(content_type__app_label="vehicles", content_type__model=model).exists():
                continue
            group.permissions.add(*Permission.objects.using(using).filter(
                content_type__app_label="vehicles", codename__in=[f"{a}_{model}" for a in actions]))
