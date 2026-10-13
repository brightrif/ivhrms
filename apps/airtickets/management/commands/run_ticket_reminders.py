from datetime import date

from django.core.management.base import BaseCommand

from apps.airtickets import reminders


class Command(BaseCommand):
    help = ("Email HR (and, if the company wants it, the employee) about air tickets that are coming up. The daily "
            "Celery job does this automatically; run it by hand only to test or on a server without Celery.")

    def add_arguments(self, parser):
        parser.add_argument("--date", help="Pretend today is YYYY-MM-DD (for testing)")

    def handle(self, *args, **opts):
        today = date.fromisoformat(opts["date"]) if opts["date"] else None
        stats = reminders.run_reminders(today)
        self.stdout.write("Air ticket reminders: {checked} checked, {reminded} reminded, {hr_emails} HR email(s), "
                          "{employee_emails} employee email(s), {errors} error(s).".format(**stats))
