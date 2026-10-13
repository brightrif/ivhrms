from datetime import date

from django.core.management.base import BaseCommand

from apps.dependants import leavers


class Command(BaseCommand):
    help = ("Email HR about family members whose sponsor is on notice or has left but who still have a current visa. "
            "The daily Celery job does this automatically; run it by hand only to test or on a server without Celery.")

    def add_arguments(self, parser):
        parser.add_argument("--date", help="Pretend today is YYYY-MM-DD (for testing)")

    def handle(self, *args, **opts):
        today = date.fromisoformat(opts["date"]) if opts["date"] else None
        stats = leavers.run_leaver_scan(today)
        self.stdout.write("Sponsors leaving: {checked} checked: {alerts} alert(s), {emails} email(s), "
                          "{errors} error(s).".format(**stats))
