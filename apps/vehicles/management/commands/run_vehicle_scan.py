from datetime import date

from django.core.management.base import BaseCommand

from apps.vehicles import maintenance


class Command(BaseCommand):
    help = ("Email any vehicle service that has become due or overdue. The daily Celery job does this "
            "automatically; run it by hand only to test or on a server without Celery.")

    def add_arguments(self, parser):
        parser.add_argument("--date", help="Pretend today is YYYY-MM-DD (for testing)")

    def handle(self, *args, **opts):
        today = date.fromisoformat(opts["date"]) if opts["date"] else None
        stats = maintenance.run_service_scan(today)
        self.stdout.write("Checked {checked} service plan(s): {alerts} alert(s), {emails} email(s), "
                          "{errors} error(s).".format(**stats))
