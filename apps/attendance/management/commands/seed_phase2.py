from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand

from apps.core.models import ApprovalFlow, ApprovalStep
from apps.leave.models import LeaveType
from apps.organization.models import Company

FLOWS = [("leave.request", "Leave request"), ("attendance.correction", "Attendance correction")]

HR_PERMISSIONS = {
    "employees": ["view_employee", "add_employee", "change_employee", "manage_logins"],
    "organization": ["view_department", "add_department", "change_department",
                     "view_designation", "add_designation", "change_designation"],
}

# Placeholders for the client to confirm: (code, name, paid, tracked, entitlement, excl_holidays, excl_weekly_offs)
LEAVE_TYPES = [
    ("ANNUAL", "Annual Leave", True, True, 30, True, False),
    ("SICK", "Sick Leave", True, False, 0, False, False),
    ("MATERNITY", "Maternity Leave", True, False, 0, False, False),
    ("PATERNITY", "Paternity Leave", True, False, 0, False, False),
    ("UNPAID", "Unpaid Leave", False, False, 0, False, False),
    ("EMERGENCY", "Emergency Leave", True, False, 0, False, False),
    ("COMP", "Compensatory Leave", True, True, 0, True, False),
]


class Command(BaseCommand):
    help = "Create the HR group, default approval flows and starter leave types (safe to re-run)."

    def handle(self, *args, **opts):
        hr, _ = Group.objects.get_or_create(name="HR")
        for app_label, codenames in HR_PERMISSIONS.items():
            hr.permissions.add(*Permission.objects.filter(
                content_type__app_label=app_label, codename__in=codenames))

        for code, name in FLOWS:
            flow, _ = ApprovalFlow.objects.get_or_create(company=None, code=code, defaults={"name": name})
            ApprovalStep.objects.get_or_create(flow=flow, order=1, defaults=dict(
                name="Reporting manager", approver_type=ApprovalStep.ApproverType.REPORTING_MANAGER))
            ApprovalStep.objects.get_or_create(flow=flow, order=2, defaults=dict(
                name="HR", approver_type=ApprovalStep.ApproverType.GROUP, group=hr))

        for company in Company.objects.filter(is_active=True):
            for code, name, paid, tracked, ent, ex_hol, ex_off in LEAVE_TYPES:
                LeaveType.objects.get_or_create(company=company, code=code, defaults=dict(
                    name=name, is_paid=paid, tracks_balance=tracked, annual_entitlement=ent,
                    exclude_holidays=ex_hol, exclude_weekly_offs=ex_off))
        self.stdout.write("Seeded.")