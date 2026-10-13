"""System data created automatically after every `migrate`. Nothing here needs a seed command.

Permissions are given to the standard groups once per model, the first time that model's permissions are missing
from the group, so anything changed later in the admin survives future migrations.

HR runs family records day to day. Finance and Management can see the family members (visa costs and
approvals need that) but not the attached papers, which include marriage certificates and medical reports."""
from django.db import DEFAULT_DB_ALIAS, connections

GROUP_PERMISSIONS = {
    "HR": {"dependant": ["view", "add", "change"], "dependantattachment": ["view", "add"]},
    "Finance": {"dependant": ["view"]},
    "Management": {"dependant": ["view"]},
}


def ensure_system_defaults(sender=None, using=DEFAULT_DB_ALIAS, **kwargs):
    tables = set(connections[using].introspection.table_names())
    if "dependants_dependant" not in tables:
        return
    from django.contrib.auth.models import Group, Permission

    for group_name, models_ in GROUP_PERMISSIONS.items():
        group, _ = Group.objects.using(using).get_or_create(name=group_name)
        for model, actions in models_.items():
            if f"dependants_{model}" not in tables:
                continue
            if group.permissions.filter(content_type__app_label="dependants", content_type__model=model).exists():
                continue
            group.permissions.add(*Permission.objects.using(using).filter(
                content_type__app_label="dependants", codename__in=[f"{a}_{model}" for a in actions]))
