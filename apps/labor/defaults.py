"""System data created automatically after every `migrate`. Nothing here needs a seed command.

Permissions are given to the standard groups once per model, the first time that model's permissions are missing
from the group, so anything changed later in the admin survives future migrations.

HR runs labor day to day, including pay rates. Finance and Management can look at everything."""
from django.db import DEFAULT_DB_ALIAS, connections

_ALL = ["trade", "contractor", "laborprofile", "laborrate", "workorder", "laborallocation", "timeentry",
        "overtimepolicy", "overtimeclaim"]
_HR = ["view", "add", "change"]
GROUP_PERMISSIONS = {
    "HR": {"trade": _HR, "contractor": _HR, "laborprofile": _HR, "laborrate": ["view", "add"],
           "workorder": _HR, "laborallocation": ["view", "add", "change"],
           "timeentry": ["view", "add", "change"],
           "overtimepolicy": ["view", "add"], "overtimeclaim": ["view", "add"]},
    "Finance": {m: ["view"] for m in _ALL},
    # Management sets the overtime rules and decides the claims; HR prepares them
    "Management": {**{m: ["view"] for m in _ALL}, "overtimepolicy": ["view", "add"],
                   "overtimeclaim": ["view", "change"]},
}


def ensure_system_defaults(sender=None, using=DEFAULT_DB_ALIAS, **kwargs):
    tables = set(connections[using].introspection.table_names())
    if "labor_laborprofile" not in tables:
        return
    from django.contrib.auth.models import Group, Permission

    for group_name, models_ in GROUP_PERMISSIONS.items():
        group, _ = Group.objects.using(using).get_or_create(name=group_name)
        for model, actions in models_.items():
            if f"labor_{model}" not in tables:
                continue
            if group.permissions.filter(content_type__app_label="labor", content_type__model=model).exists():
                continue
            group.permissions.add(*Permission.objects.using(using).filter(
                content_type__app_label="labor", codename__in=[f"{a}_{model}" for a in actions]))
