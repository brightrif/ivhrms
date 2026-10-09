"""Dividing a worker's days between projects, for many days at once: the month-end tool for hours.

One way of dividing a day is applied to every worked day in a period. Each project gets its regular hours and, if it
asked for any, its own overtime. For a worker who serves every project, the day's standard hours can instead be
shared evenly. The day's regular hours together never pass the day's standard hours, overtime is never shared, and
a day with confirmed hours is left alone."""
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from . import timekeeping
from .allocation import LaborAllocation
from .services import LaborError
from .timesheet import TimeEntry

ZERO, QUARTER = Decimal("0"), Decimal("0.25")
MAX_DAYS = 62


def even_shares(total, count):
    """Share `total` hours between `count` projects in quarter hours. The first project takes what is left over."""
    base = (total / count // QUARTER) * QUARTER
    return [total - base * (count - 1)] + [base] * (count - 1)


def _site_for(employee, project, first, last):
    """The site the worker is on for this project in the period, or the project's own site."""
    allocation = (LaborAllocation.objects.filter(employee=employee, project=project, effective_from__lte=last)
                  .filter(Q(effective_to__isnull=True) | Q(effective_to__gte=first)).select_related("location")
                  .order_by("-effective_from").first())
    return allocation.location if allocation else project.location


def split_days(user, employee, lines, first, last, *, even=False, today=None):
    """Apply one way of dividing the day to every worked day from `first` to `last`.

    `lines` is a list of {"project": Project, "regular": Decimal, "overtime": Decimal}; regular is ignored when
    even=True. The worker's draft hours on those days are replaced. Returns
    {"days": days divided, "lines": lines written, "skipped": [text]}."""
    today = today or timezone.localdate()
    lines = [dict(line) for line in lines]
    if not lines:
        raise LaborError("Choose at least one project.")
    if len({line["project"].pk for line in lines}) != len(lines):
        raise LaborError("Each project can be chosen once.")
    for line in lines:
        line["regular"], line["overtime"] = line.get("regular") or ZERO, line.get("overtime") or ZERO
        if line["regular"] < 0 or line["overtime"] < 0:
            raise LaborError("Hours cannot be negative.")
    if not even and not any(line["regular"] + line["overtime"] >= timekeeping.MIN_HOURS for line in lines):
        raise LaborError("Enter the hours for at least one project.")
    if last < first:
        raise LaborError("The last day cannot be before the first day.")
    if (last - first).days >= MAX_DAYS:
        raise LaborError(f"Choose {MAX_DAYS} days or fewer at a time.")

    cells_by_project = {}
    for line in lines:
        project = line["project"]
        line["site"] = _site_for(employee, project, first, last)
        if line["site"] is None:
            raise LaborError(f"{project.code} has no site yet.")
        sheet = timekeeping.build_timesheet(user, project, line["site"], first, last, today)
        row = next((r for r in sheet.rows if r.employee.pk == employee.pk), None)
        if row is None:
            raise LaborError(f"{employee.full_name} is not on {project.code}. "
                             "Add them on that project's Team page first.")
        cells_by_project[project.pk] = {c.date: c for c in row.cells}

    result = {"days": 0, "lines": 0, "skipped": []}
    for offset in range((last - first).days + 1):
        day = first + timedelta(days=offset)
        cells = [cells_by_project[line["project"].pk][day] for line in lines]
        if day > today or not cells[0].worked:
            continue                                            # not a worked day: nothing to divide
        problem = None
        if any(not c.covered for c in cells):
            problem = "the worker was not on every chosen project that day"
        elif any(c.expected is None for c in cells):
            problem = "no pay terms that day"
        elif TimeEntry.objects.filter(employee=employee, date=day, status=TimeEntry.Status.CONFIRMED).exists():
            problem = "the hours are confirmed"
        if problem is None:
            standard = cells[0].expected
            regular = even_shares(standard, len(lines)) if even else [line["regular"] for line in lines]
            overtime = [line["overtime"] for line in lines]
            if any(overtime) and not all(c.eligible for c in cells):
                problem = "not eligible for overtime that day"
            elif sum(regular) > standard:
                problem = (f"regular hours {timekeeping._fmt(sum(regular))} are more than the "
                           f"{timekeeping._fmt(standard)} the day allows")
        if problem:
            result["skipped"].append(f"{day:%d %b}: {problem}")
            continue
        with transaction.atomic():
            for old in TimeEntry.objects.filter(employee=employee, date=day, status=TimeEntry.Status.DRAFT):
                old.delete()                                    # one by one, so the audit log sees each
            for line, cell, reg, ot in zip(lines, cells, regular, overtime):
                hours = reg + ot
                if hours < timekeeping.MIN_HOURS:
                    continue
                TimeEntry.objects.create(employee=employee, date=day, project=line["project"], location=line["site"],
                                         work_order_id=cell.work_order_id, hours=hours, expected_hours=reg,
                                         overtime_eligible=cell.eligible)
                result["lines"] += 1
        result["days"] += 1
    return result
