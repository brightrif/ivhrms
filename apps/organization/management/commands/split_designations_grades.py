from django.core.management.base import BaseCommand

from apps.organization.company_lists import split_shared


class Command(BaseCommand):
    help = ("Give each company its own copy of every shared designation and grade, move each company's staff onto "
            "its own copy, and remove the shared row. Without --apply it only shows what it would do.")

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Do it. Without this nothing is changed.")

    def handle(self, *args, **options):
        apply = options["apply"]
        report = split_shared(apply=apply)
        if not report:
            self.stdout.write("Nothing to do: no designation or grade is shared.")
            return
        self.stdout.write(("Done." if apply else "Preview only. Nothing was changed. Add --apply to do it.") + "\n")
        for row in report:
            made = ", ".join(row["new"]) or "none needed"
            ending = "shared row removed" if row["deleted"] else f"shared row kept ({row['left']})"
            self.stdout.write(f"  {row['kind']:<12} {row['name']:<30} copies for: {made}; "
                              f"{row['moved']} record(s) moved; {ending}")
