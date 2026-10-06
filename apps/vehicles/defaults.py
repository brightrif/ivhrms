"""System data created automatically after every `migrate`. Nothing here needs a seed command.

Permissions are given to the standard groups once per model, the first time that model's permissions are
missing from the group, so anything changed later in the admin survives future migrations (and a model
added in a later phase still gets its permissions).
"""
from django.db import DEFAULT_DB_ALIAS, connections

_HR = ["view", "add", "change"]
_MODELS = ["vehicle", "vehicleassignment", "odometerreading", "fuelfill", "serviceplan", "servicerecord"]
GROUP_PERMISSIONS = {
    "HR": {"vehicle": _HR, "vehicleassignment": _HR, "odometerreading": ["view", "add"],
           "fuelfill": _HR, "serviceplan": _HR, "servicerecord": _HR},
    "Finance": {m: ["view"] for m in _MODELS},
    "Management": {m: ["view"] for m in _MODELS},
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
