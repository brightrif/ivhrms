"""System data created automatically after every `migrate`. Nothing here needs a seed command.

Permissions are given to the standard groups once per model, the first time that model's permissions are missing
from the group, so anything changed later in the admin survives future migrations.

HR runs family records and visa requests day to day. Finance records what visas cost. Finance and Management can
see the family members and the requests but not the attached papers (marriage certificates, medical reports).

The visa approval flow is the reporting manager, then HR. It can be edited with the other approval flows; an
existing one is never overwritten."""
from django.db import DEFAULT_DB_ALIAS, connections

FLOW_CODE = "dependants.visa"

GROUP_PERMISSIONS = {
    "HR": {"dependant": ["view", "add", "change"], "dependantattachment": ["view", "add"],
           "familyvisarequest": ["view", "add", "change"], "visapayment": ["view", "add"],
           "familyvisaexception": ["view", "add", "change"]},
    "Finance": {"dependant": ["view"], "familyvisarequest": ["view"], "visapayment": ["view", "add"]},
    "Management": {"dependant": ["view"], "familyvisarequest": ["view"], "visapayment": ["view"],
                   "familyvisaexception": ["view", "add", "change"]},
}


def _ensure_visa_flow(using, tables):
    if not {"core_approvalflow", "core_approvalstep"} <= tables:
        return
    from django.contrib.auth.models import Group

    from apps.core.models import ApprovalFlow, ApprovalStep

    if ApprovalFlow.objects.using(using).filter(code=FLOW_CODE, company__isnull=True).exists():
        return
    hr, _ = Group.objects.using(using).get_or_create(name="HR")
    # bulk_create sends no save signals, on purpose: this is system data, not something a person did
    flow = ApprovalFlow.objects.using(using).bulk_create([ApprovalFlow(code=FLOW_CODE, name="Family visa request")])[0]
    ApprovalStep.objects.using(using).bulk_create([
        ApprovalStep(flow=flow, order=1, name="Reporting manager",
                     approver_type=ApprovalStep.ApproverType.REPORTING_MANAGER),
        ApprovalStep(flow=flow, order=2, name="HR", approver_type=ApprovalStep.ApproverType.GROUP, group=hr)])


def ensure_system_defaults(sender=None, using=DEFAULT_DB_ALIAS, **kwargs):
    tables = set(connections[using].introspection.table_names())
    if "dependants_dependant" not in tables:
        return
    _ensure_visa_flow(using, tables)
    from .status_defaults import ensure_status_flow

    ensure_status_flow(using, tables)
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