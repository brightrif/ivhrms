from datetime import date

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.attendance import services
from apps.organization.models import Company


class Command(BaseCommand):
    help = "Create Holiday / Weekly Off rows for employees with no attendance entry on a date."

    def add_arguments(self, parser):
        parser.add_argument("--date", help="YYYY-MM-DD (default: today)")
        parser.add_argument("--company", help="Company code (default: all)")

    def handle(self, *args, **opts):
        d = date.fromisoformat(opts["date"]) if opts["date"] else timezone.localdate()
        companies = Company.objects.filter(is_active=True)
        if opts["company"]:
            companies = companies.filter(code=opts["company"])
        for c in companies:
            self.stdout.write(f"{c.code}: {services.fill_non_working_days(c, d)} row(s) created for {d}")