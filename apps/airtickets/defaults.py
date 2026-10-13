"""System data created automatically after every `migrate`: who may do what with air tickets.

Given to a group once per model, the first time that model's permissions are missing from the group, so anything
changed later in the admin survives future migrations."""
from django.db import DEFAULT_DB_ALIAS, connections

FLOW_CODE = "airtickets.request"

GROUP_PERMISSIONS = {
    "HR": {"ticketpolicy": ["view", "add", "change"], "ticketentitlement": ["view", "add", "change"],
           "travelprofile": ["view", "add", "change"], "ticketrequest": ["view", "add", "change"]},
    "Finance": {"ticketpolicy": ["view"], "ticketentitlement": ["view"], "travelprofile": ["view"],
                "ticketrequest": ["view"]},
    "Management": {"ticketpolicy": ["view"], "ticketentitlement": ["view"], "travelprofile": ["view"],
                   "ticketrequest": ["view"]},
}


def _ensure_request_flow(using, tables):
    """The approval for an air ticket request: the reporting manager, then HR. An existing one is never overwritten."""
    if not {"core_approvalflow", "core_approvalstep"} <= tables:
        return
    from django.contrib.auth.models import Group

    from apps.core.models import ApprovalFlow, ApprovalStep

    if ApprovalFlow.objects.using(using).filter(code=FLOW_CODE, company__isnull=True).exists():
        return
    hr, _ = Group.objects.using(using).get_or_create(name="HR")
    # bulk_create sends no save signals, on purpose: this is system data, not something a person did
    flow = ApprovalFlow.objects.using(using).bulk_create([ApprovalFlow(code=FLOW_CODE, name="Air ticket request")])[0]
    ApprovalStep.objects.using(using).bulk_create([
        ApprovalStep(flow=flow, order=1, name="Reporting manager",
                     approver_type=ApprovalStep.ApproverType.REPORTING_MANAGER),
        ApprovalStep(flow=flow, order=2, name="HR", approver_type=ApprovalStep.ApproverType.GROUP, group=hr)])


def ensure_system_defaults(sender=None, using=DEFAULT_DB_ALIAS, **kwargs):
    tables = set(connections[using].introspection.table_names())
    if "airtickets_ticketpolicy" not in tables:
        return
    _ensure_request_flow(using, tables)
    from django.contrib.auth.models import Group, Permission

    for group_name, models_ in GROUP_PERMISSIONS.items():
        group, _ = Group.objects.using(using).get_or_create(name=group_name)
        for model, actions in models_.items():
            if f"airtickets_{model}" not in tables:
                continue
            if group.permissions.filter(content_type__app_label="airtickets", content_type__model=model).exists():
                continue
            group.permissions.add(*Permission.objects.using(using).filter(
                content_type__app_label="airtickets", codename__in=[f"{a}_{model}" for a in actions]))
