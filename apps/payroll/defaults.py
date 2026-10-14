"""System data created automatically after every `migrate`. Nothing here needs a seed command.

The payroll approval flow: one step, anyone in Management. It can be edited with the other approval flows; an
existing one is never overwritten. Nobody is given payroll rights here: the superuser chooses on the Access tab."""
from django.db import DEFAULT_DB_ALIAS, connections

FLOW_CODE = "payroll.run"


def ensure_system_defaults(sender=None, using=DEFAULT_DB_ALIAS, **kwargs):
    tables = set(connections[using].introspection.table_names())
    if not {"core_approvalflow", "core_approvalstep", "payroll_payrollrun"} <= tables:
        return
    from django.contrib.auth.models import Group

    from apps.core.models import ApprovalFlow, ApprovalStep

    group, _ = Group.objects.using(using).get_or_create(name="Management")
    for code, name in ((FLOW_CODE, "Payroll approval"), ("payroll.settlement", "Final settlement approval")):
        if ApprovalFlow.objects.using(using).filter(code=code, company__isnull=True).exists():
            continue
        # bulk_create sends no save signals, on purpose: this is system data, not something a person did
        flow = ApprovalFlow.objects.using(using).bulk_create([ApprovalFlow(code=code, name=name)])[0]
        ApprovalStep.objects.using(using).bulk_create([ApprovalStep(
            flow=flow, order=1, name="Management approval", approver_type=ApprovalStep.ApproverType.GROUP, group=group)])
