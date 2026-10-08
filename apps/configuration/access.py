"""The Access grid: who may do what, chosen by the superuser.

It reads and writes ordinary group permissions, so it always agrees with the Django admin. Only the rights listed in
the registry are ever touched; every other permission a group has is left exactly as it is."""
from dataclasses import dataclass

from django.contrib.auth.models import Group, Permission
from django.db import transaction

from apps.audit import services as audit

from . import registry


@dataclass
class Row:
    module: registry.Module
    label: str
    perm: str                      # "app_label.codename"
    hint: str = ""
    granted: frozenset = frozenset()   # group ids that have it now


def _split(perm):
    app_label, codename = perm.split(".", 1)
    return app_label, codename


def rows():
    """Every right in the registry, module by module: the module's own two settings rights, then its capabilities."""
    out = []
    for module in registry.modules():
        out.append(Row(module, f"See {module.label} settings", module.view_perm))
        out.append(Row(module, f"Change {module.label} settings", module.edit_perm,
                       "Includes the simple switches on the tab. Rates and approvals have their own rights."))
        out.extend(Row(module, c.label, c.perm, c.hint) for c in module.capabilities)
    return out


def _permission(perm):
    app_label, codename = _split(perm)
    return Permission.objects.filter(content_type__app_label=app_label, codename=codename).first()


def grid():
    """(groups, rows with `granted` filled in). Rights whose permission does not exist yet are left out."""
    groups = list(Group.objects.order_by("name"))
    shown = []
    for row in rows():
        permission = _permission(row.perm)
        if permission is None:
            continue
        row.granted = frozenset(Group.objects.filter(permissions=permission).values_list("pk", flat=True))
        shown.append(row)
    return groups, shown


@transaction.atomic
def apply(actor, wanted):
    """Make the groups match `wanted`, a set of (perm, group_id) that should be granted. Returns (granted, revoked)."""
    groups = {g.pk: g for g in Group.objects.all()}
    granted = revoked = 0
    for row in rows():
        permission = _permission(row.perm)
        if permission is None:
            continue
        have = set(Group.objects.filter(permissions=permission).values_list("pk", flat=True))
        for group_id, group in groups.items():
            want = (row.perm, group_id) in wanted
            if want and group_id not in have:
                group.permissions.add(permission)
                granted += 1
                audit.log("update", group, module="configuration", subject_employee_id=None, company_id=None,
                          reason=f"{actor.get_username()} gave group '{group.name}' the right: {row.label} ({row.perm})")
            elif not want and group_id in have:
                group.permissions.remove(permission)
                revoked += 1
                audit.log("update", group, module="configuration", subject_employee_id=None, company_id=None,
                          reason=f"{actor.get_username()} took from group '{group.name}' the right: {row.label} ({row.perm})")
    return granted, revoked
