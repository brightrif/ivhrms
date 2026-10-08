"""The site attendance sheet: one grid per site and period, filled in by HR daily, weekly or at month end.

Entries are saved through the normal attendance service, so every rule HR already knows still applies (no future
dates, nothing before the joining date, an existing day is never overwritten, and everything is audited). Days that
are already saved are shown but not changed here."""
from calendar import monthrange
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from django.db import IntegrityError
from django.db.models import Q
from django.utils import timezone

from apps.attendance import services as attendance
from apps.attendance.models import Attendance
from apps.scheduling.services import HOLIDAY, WEEKLY_OFF, day_types

from .allocation import LaborAllocation
from .services import LaborError

Status = Attendance.Status
# what HR can enter on the sheet; leave, holidays and corrections have their own routes
ENTRY = {Status.PRESENT.value: "P", Status.ABSENT.value: "A", Status.HALF_DAY.value: "\u00bd"}
SHORT = {"present": "P", "late_entry": "P", "early_exit": "P", "absent": "A", "half_day": "\u00bd",
         "paid_leave": "L", "unpaid_leave": "UL", "holiday": "Hol", "weekly_off": "Off"}
PERIODS = {"day": "Day", "week": "Week (Sun to Sat)", "month": "Month"}
OFF_DAYS = (HOLIDAY, WEEKLY_OFF)


def period_bounds(period, anchor):
    """First and last day of the day, the Sunday-to-Saturday week, or the calendar month that holds `anchor`."""
    if period == "day":
        return anchor, anchor
    if period == "month":
        return anchor.replace(day=1), anchor.replace(day=monthrange(anchor.year, anchor.month)[1])
    start = anchor - timedelta(days=(anchor.weekday() + 1) % 7)          # Sunday
    return start, start + timedelta(days=6)


def shift_anchor(period, anchor, step):
    """The anchor date of the previous (-1) or next (+1) period."""
    if period == "day":
        return anchor + timedelta(days=step)
    if period == "week":
        return anchor + timedelta(days=7 * step)
    month = anchor.month - 1 + step
    return date(anchor.year + month // 12, month % 12 + 1, 1)


def sites_in_period(user, first, last):
    """(project, location) pairs that had workers allocated at some point in the period."""
    rows = (LaborAllocation.objects.for_user(user).filter(effective_from__lte=last)
            .filter(Q(effective_to__isnull=True) | Q(effective_to__gte=first))
            .select_related("project", "location").order_by("project__code", "location__name"))
    seen, out = set(), []
    for a in rows:
        if (a.project_id, a.location_id) not in seen:
            seen.add((a.project_id, a.location_id))
            out.append((a.project, a.location))
    return out


@dataclass
class Cell:
    date: date
    kind: str
    record: Attendance = None
    covered: bool = False                # the worker was allocated to this site that day
    editable: bool = False
    key: str = ""

    @property
    def short(self):
        return SHORT.get(self.record.status, "?") if self.record else ""


@dataclass
class Row:
    employee: object
    profile: object
    cells: list = field(default_factory=list)

    @property
    def worked(self):
        return sum((Decimal(str(c.record.day_fraction)) for c in self.cells
                    if c.record and c.record.status in Attendance.WORKED), Decimal("0"))

    @property
    def absent(self):
        return sum(1 for c in self.cells if c.record and c.record.status == Status.ABSENT)

    @property
    def missing(self):
        """Past working days at this site with nothing recorded yet."""
        return sum(1 for c in self.cells if c.editable and c.kind not in OFF_DAYS)


@dataclass
class Sheet:
    project: object
    location: object
    first: date
    last: date
    days: list
    rows: list


def build_sheet(user, project, location, first, last, today=None):
    today = today or timezone.localdate()
    days = [first + timedelta(days=i) for i in range((last - first).days + 1)]
    allocations = (LaborAllocation.objects.for_user(user).filter(project=project, location=location,
                                                                 effective_from__lte=last)
                   .filter(Q(effective_to__isnull=True) | Q(effective_to__gte=first))
                   .select_related("employee", "employee__labor_profile__trade", "employee__labor_profile__contractor")
                   .order_by("employee__employee_no"))
    by_employee = defaultdict(list)
    for a in allocations:
        by_employee[a.employee_id].append(a)
    records = defaultdict(dict)
    for rec in Attendance.objects.filter(employee_id__in=list(by_employee), date__range=(first, last)):
        records[rec.employee_id][rec.date] = rec
    rows = []
    for employee_id, spans in by_employee.items():
        employee = spans[0].employee
        kinds = day_types(employee, first, last)
        row = Row(employee=employee, profile=getattr(employee, "labor_profile", None))
        for d in days:
            covered = any(a.effective_from <= d and (a.effective_to is None or d <= a.effective_to) for a in spans)
            rec = records[employee_id].get(d)
            editable = covered and rec is None and d <= today and d >= employee.joining_date
            row.cells.append(Cell(date=d, kind=kinds[d], record=rec, covered=covered, editable=editable,
                                  key=f"c_{employee_id}_{d:%Y%m%d}"))
        rows.append(row)
    rows.sort(key=lambda r: r.employee.employee_no)
    return Sheet(project=project, location=location, first=first, last=last, days=days, rows=rows)


def save_sheet(user, project, location, first, last, posted, fill=""):
    """Save what HR entered. `posted` maps cell keys to a status. `fill` (a status or "") is applied to every empty
    working day that was left blank, so HR only types the exceptions. Only cells the server itself offers are read.
    Returns {"created": Counter, "skipped": [text]}."""
    if fill and fill not in ENTRY:
        raise LaborError("Choose Present, Absent or Half day to fill the empty days.")
    sheet = build_sheet(user, project, location, first, last)
    created, skipped = Counter(), []
    remarks = f"Site sheet {project.code} / {location.name}"[:255]
    for row in sheet.rows:
        for cell in row.cells:
            if not cell.editable:
                continue
            value = posted.get(cell.key, "")
            if value not in ENTRY:
                value = ""
            if not value and fill and cell.kind not in OFF_DAYS:
                value = fill
            if not value:
                continue
            try:
                attendance.mark_attendance(row.employee, cell.date, value, project=project, location=location,
                                           remarks=remarks, source=Attendance.Source.BULK)
            except (attendance.AttendanceError, IntegrityError) as exc:
                skipped.append(f"{row.employee.employee_no} {cell.date:%d %b}: {exc}")
            else:
                created[value] += 1
    return {"created": created, "skipped": skipped}
