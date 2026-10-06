from datetime import date

from django.core.management.base import BaseCommand

from apps.compliance import services


class Command(BaseCommand):
    help = ("Check document expiries and send any alerts that are due. The daily Celery job does this "
            "automatically; run it by hand only to test or on a server without Celery.")

    def add_arguments(self, parser):
        parser.add_argument("--date", help="Pretend today is YYYY-MM-DD (for testing)")

    def handle(self, *args, **opts):
        today = date.fromisoformat(opts["date"]) if opts["date"] else None
        stats = services.run_daily_scan(today)
        self.stdout.write("Checked {checked} document(s): {alerts} alert(s), {emails} email(s), "
                          "{tasks} renewal task(s) opened, {errors} error(s).".format(**stats))
