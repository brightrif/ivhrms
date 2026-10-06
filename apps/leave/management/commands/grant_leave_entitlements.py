from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.employees.models import Employee
from apps.leave import services
from apps.leave.models import LeaveType


class Command(BaseCommand):
    help = "Grant yearly leave entitlements to all active employees (safe to re-run)."

    def add_arguments(self, parser):
        parser.add_argument("--year", type=int, default=timezone.localdate().year)
        parser.add_argument("--company", help="Company code (default: all)")

    def handle(self, *args, **opts):
        year, granted = opts["year"], 0
        types = LeaveType.objects.filter(is_active=True, tracks_balance=True, annual_entitlement__gt=0)
        if opts["company"]:
            types = types.filter(company__code=opts["company"])
        for lt in types.select_related("company"):
            employees = Employee.objects.filter(company=lt.company).exclude(status=Employee.Status.SEPARATED)
            for emp in employees:
                if services.grant_entitlement(emp, lt, year):
                    granted += 1
        self.stdout.write(f"{granted} entitlement(s) granted for {year}")