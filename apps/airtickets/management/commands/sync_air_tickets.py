from django.core.management.base import BaseCommand

from apps.airtickets import services


class Command(BaseCommand):
    help = ("Work out every employee's next air ticket from the company policies. The daily Celery job does this "
            "automatically, and so does saving a policy; run it by hand only to test or without Celery.")

    def handle(self, *args, **opts):
        stats = services.sync_all()
        self.stdout.write("{checked} staff checked: {with_policy} with a ticket policy, "
                          "{without_policy} without.".format(**stats))
