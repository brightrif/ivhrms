"""System data created automatically after every `migrate`. Nothing here needs a seed command.

Permissions are given to the standard groups once per model, the first time that model's permissions are
missing from the group, so anything changed later in the admin survives future migrations (and a model
added in a later phase still gets its permissions).

Loan details are for Finance and Management only: HR is deliberately left out.
"""
from django.db import DEFAULT_DB_ALIAS, connections

_HR = ["view", "add", "change"]
_FLEET = ["vehicle", "vehicleassignment", "odometerreading", "fuelfill", "serviceplan", "servicerecord",
          "fine", "accident", "custodyrequest"]
_LOANS = ["vehicleloan", "loaninstallment"]
GROUP_PERMISSIONS = {
    "HR": {"vehicle": _HR, "vehicleassignment": _HR, "odometerreading": ["view", "add"], "fuelfill": _HR,
           "serviceplan": _HR, "servicerecord": _HR, "fine": _HR + ["pay"], "accident": _HR, "custodyrequest": _HR},
    "Finance": {**{m: ["view"] for m in _FLEET}, "fine": ["view", "pay"],
                "vehicleloan": _HR, "loaninstallment": ["view", "pay"]},          # Finance runs the loans
    "Management": {m: ["view"] for m in _FLEET + _LOANS},
}


FLOW_CODE = "vehicles.custody"


def _ensure_custody_flow(using, tables):
    """The approval flow for custody decisions: one step, anyone in Management. It can be edited with the other
    approval flows; an existing one is never overwritten."""
    if not {"core_approvalflow", "core_approvalstep"} <= tables:
        return
    from django.contrib.auth.models import Group

    from apps.core.models import ApprovalFlow, ApprovalStep

    if ApprovalFlow.objects.using(using).filter(code=FLOW_CODE, company__isnull=True).exists():
        return
    group, _ = Group.objects.using(using).get_or_create(name="Management")
    # bulk_create sends no save signals, on purpose: this is system data, not something a person did
    flow = ApprovalFlow.objects.using(using).bulk_create([ApprovalFlow(code=FLOW_CODE, name="Vehicle custody decision")])[0]
    ApprovalStep.objects.using(using).bulk_create([ApprovalStep(
        flow=flow, order=1, name="Management approval", approver_type=ApprovalStep.ApproverType.GROUP, group=group)])


def ensure_system_defaults(sender=None, using=DEFAULT_DB_ALIAS, **kwargs):
    tables = set(connections[using].introspection.table_names())
    if "vehicles_vehicle" not in tables:
        return
    _ensure_custody_flow(using, tables)
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
