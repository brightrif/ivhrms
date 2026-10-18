"""System data for family status requests, created automatically after every `migrate` (called from defaults.py).

The approval is one step: the employee's reporting manager. (The HR review of an employee's request is part of the
request itself.) It can be edited with the other approval flows; an existing one is never overwritten.

HR runs the requests. Management can see them. Rights are given to a group once per model, the first time that
model's permissions are missing from it, so anything changed later in the admin survives future migrations."""
FLOW_CODE = "dependants.status"

GROUP_PERMISSIONS = {
    "HR": ["view", "add", "change"],
    "Management": ["view"],
}


def ensure_status_flow(using, tables):
    if {"core_approvalflow", "core_approvalstep"} <= tables:
        from apps.core.models import ApprovalFlow, ApprovalStep

        if not ApprovalFlow.objects.using(using).filter(code=FLOW_CODE, company__isnull=True).exists():
            # bulk_create sends no save signals, on purpose: this is system data, not something a person did
            flow = ApprovalFlow.objects.using(using).bulk_create(
                [ApprovalFlow(code=FLOW_CODE, name="Family status request")])[0]
            ApprovalStep.objects.using(using).bulk_create([
                ApprovalStep(flow=flow, order=1, name="Reporting manager",
                             approver_type=ApprovalStep.ApproverType.REPORTING_MANAGER)])
    if "dependants_familystatusrequest" in tables:
        from django.contrib.auth.models import Group, Permission

        for group_name, actions in GROUP_PERMISSIONS.items():
            group, _ = Group.objects.using(using).get_or_create(name=group_name)
            if group.permissions.filter(content_type__app_label="dependants",
                                        content_type__model="familystatusrequest").exists():
                continue
            group.permissions.add(*Permission.objects.using(using).filter(
                content_type__app_label="dependants",
                codename__in=[f"{a}_familystatusrequest" for a in actions]))
