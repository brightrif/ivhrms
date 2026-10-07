from datetime import date

from django.core.management.base import BaseCommand

from apps.vehicles import financing, finereminders, handovers, maintenance


class Command(BaseCommand):
    help = ("Email any vehicle service that has become due or overdue, any loan installment that is due soon or "
            "overdue, a notice about vehicles held by people on notice or who have left, and the weekly reminder "
            "about unpaid fines. The daily Celery job does this automatically; run it by hand only to test or on a "
            "server without Celery.")

    def add_arguments(self, parser):
        parser.add_argument("--date", help="Pretend today is YYYY-MM-DD (for testing)")

    def handle(self, *args, **opts):
        today = date.fromisoformat(opts["date"]) if opts["date"] else None
        line = "{checked} checked: {alerts} alert(s), {emails} email(s), {errors} error(s)."
        self.stdout.write("Service plans: " + line.format(**maintenance.run_service_scan(today)))
        self.stdout.write("Loan installments: " + line.format(**financing.run_loan_scan(today)))
        self.stdout.write("Holders leaving: " + line.format(**handovers.run_leaver_scan(today)))
        self.stdout.write("Unpaid fines: " + line.format(**finereminders.run_fine_scan(today)))
