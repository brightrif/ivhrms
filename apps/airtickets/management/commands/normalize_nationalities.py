from collections import Counter

from django.core.management.base import BaseCommand

from apps.airtickets.nationalities import canonical
from apps.employees.models import Employee


class Command(BaseCommand):
    help = ("Rewrite the nationality of existing employees to one spelling ('india' -> 'Indian'). Shows what it WOULD do "
            "unless you add --apply. Anything it does not recognise is left alone and listed, so you can fix it by hand.")

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Really change the employees")

    def handle(self, *args, **opts):
        changes, unknown, blank = Counter(), Counter(), 0
        for emp in Employee.objects.all().only("id", "nationality"):
            text = (emp.nationality or "").strip()
            if not text:
                blank += 1
                continue
            fixed = canonical(text)
            if fixed is None:
                unknown[text] += 1
            elif fixed != emp.nationality:
                changes[(emp.nationality, fixed)] += 1
                if opts["apply"]:
                    Employee.objects.filter(pk=emp.pk).update(nationality=fixed)
        verb = "Changed" if opts["apply"] else "Would change"
        for (old, new), n in sorted(changes.items()):
            self.stdout.write(f"{verb} {n} x {old!r} -> {new!r}")
        if not changes:
            self.stdout.write("Nothing to change.")
        if unknown:
            self.stdout.write("Not recognised (left as typed): " + ", ".join(f"{t!r} x{n}" for t, n in sorted(unknown.items())))
        self.stdout.write(f"{blank} employee(s) have no nationality.")
        if changes and not opts["apply"]:
            self.stdout.write("Run again with --apply to make the changes.")
