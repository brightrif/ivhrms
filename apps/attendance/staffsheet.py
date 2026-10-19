"""Staff attendance entered by HR as a grid: people down the side, the days of a day, week or month across.

Entries go through the normal attendance service, so every rule still applies: nothing in the future, nothing before
the joining date, check-out after check-in, and an entry that already exists is never overwritten (a change to it needs
a correction that the manager approves). The one exception is the system's own weekly-off or holiday row, which HR may
replace when someone worked that day (see restdays.py).

The page builds a plan first. The plan is shown for confirmation and is worked out again from the same form when it is
confirmed, so nothing is trusted from the review page. The plan also lists the weekly offs and holidays in the period
that have no record yet; they are recorded together with the entries."""
from calendar import monthrange
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from django.db import IntegrityError
from django.db.models import Q
from django.utils import timezone

from apps.employees.models import Employee
from apps.scheduling.services import HOLIDAY, WEEKLY_OFF, day_types, holidays_between, shift_on

from . import restdays, services
from .models import Attendance

Status = Attendance.Status
ENTRY = {Status.PRESENT.value: "Present", Status.HALF_DAY.value: "Half day", Status.ABSENT.value: "Absent"}
SHORT = {"present": "P", "late_entry": "P", "early_exit": "P", "absent": "A", "half_day": "\u00bd",
         "paid_leave": "L", "unpaid_leave": "UL", "holiday": "Hol", "weekly_off": "Off"}
PERIODS = {"day": "Day", "week": "Week (Sunday to Saturday)", "month": "Month"}
OFF_DAYS = (HOLIDAY, WEEKLY_OFF)
LIMIT = 300


class StaffSheetError(Exception):
    pass


# ---------------------------------------------------------------- periods (the same rules as the labor sheets)

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


# ---------------------------------------------------------------- the sheet

@dataclass
class Cell:
    date: date
    kind: str                       # working, weekly_off or holiday, for this person
    key: str
    record: object = None
    editable: bool = False          # empty and allowed, or the system's rest-day row that HR may replace
    rest_row: bool = False          # the system's weekly-off or holiday row
    before: bool = False            # before the joining date
    future: bool = False
    holiday: str = ""               # the holiday's name
    value: str = ""                 # what was typed (kept when the page comes back to be edited)

    @property
    def short(self):
        return SHORT.get(self.record.status, "?") if self.record else ""


@dataclass
class Row:
    employee: object
    cells: list
    check_in: str = ""
    check_out: str = ""

    @property
    def open_days(self):
        """Past working days with nothing recorded yet."""
        return sum(1 for c in self.cells if c.editable and not c.rest_row and c.kind == "working")

    @property
    def absent(self):
        return sum(1 for c in self.cells if c.record and c.record.status == Status.ABSENT)


@dataclass
class Sheet:
    first: date
    last: date
    days: list
    rows: list
    holidays: dict = field(default_factory=dict)       # {date: name}


def staff_for(user, last, q="", department_id=None, employee_id=None):
    """Staff (never labour) who had joined by the end of the period and are still with the company."""
    qs = (Employee.objects.for_user(user).exclude(status=Employee.Status.SEPARATED)
          .exclude(worker_type=Employee.WorkerType.LABOR).filter(joining_date__lte=last)
          .select_related("department", "designation"))
    if employee_id:
        qs = qs.filter(pk=employee_id)
    if department_id:
        qs = qs.filter(department_id=department_id)
    for word in (q or "").split():
        qs = qs.filter(Q(first_name__icontains=word) | Q(last_name__icontains=word)
                       | Q(employee_no__icontains=word))
    return list(qs.order_by("employee_no")[:LIMIT])


def cell_key(employee, d):
    return f"c_{employee.pk}_{d:%Y%m%d}"


def build(user, first, last, posted=None, **filters):
    today = timezone.localdate()
    staff = staff_for(user, last, **filters)
    records = defaultdict(dict)
    for rec in Attendance.objects.filter(employee_id__in=[e.pk for e in staff], date__range=(first, last)):
        records[rec.employee_id][rec.date] = rec
    names, per_company = {}, {}
    days = [first + timedelta(days=i) for i in range((last - first).days + 1)]
    rows = []
    for emp in staff:
        if emp.company_id not in per_company:
            per_company[emp.company_id] = holidays_between(emp.company_id, first, last)
            names.update({d: h.name for d, h in per_company[emp.company_id].items()})
        kinds = day_types(emp, first, last)
        cells = []
        for d in days:
            rec = records[emp.pk].get(d)
            before, future = d < emp.joining_date, d > today
            rest = bool(rec and rec.source == Attendance.Source.SYSTEM and rec.status in restdays.REST_STATUSES
                        and not rec.is_locked and not before and not future)
            cell = Cell(date=d, kind=kinds[d], key=cell_key(emp, d), record=rec, rest_row=rest, before=before,
                        future=future, editable=(rec is None and not before and not future) or rest,
                        holiday=per_company[emp.company_id][d].name if d in per_company[emp.company_id] else "")
            if posted is not None and cell.editable:
                typed = posted.get(cell.key, "")
                cell.value = typed if typed in ENTRY else ""
            cells.append(cell)
        row = Row(emp, cells)
        if posted is not None:
            row.check_in, row.check_out = posted.get(f"i_{emp.pk}", ""), posted.get(f"o_{emp.pk}", "")
        rows.append(row)
    return Sheet(first, last, days, rows, names)


# ---------------------------------------------------------------- the plan

@dataclass
class Entry:
    employee: object
    date: date
    status: str
    check_in: object = None
    check_out: object = None
    replaces_rest: bool = False


@dataclass
class Plan:
    first: date
    last: date
    entries: list = field(default_factory=list)
    rest: list = field(default_factory=list)          # [(employee, date, kind)]: weekly offs and holidays to record
    skipped: list = field(default_factory=list)

    @property
    def counts(self):
        counted = Counter(e.status for e in self.entries)
        return [(label, counted[key]) for key, label in ENTRY.items() if counted[key]]

    @property
    def rest_counts(self):
        counted = Counter(kind for _, _, kind in self.rest)
        return [(label, counted[kind]) for kind, label in ((WEEKLY_OFF, "weekly offs"), (HOLIDAY, "holidays"))
                if counted[kind]]

    @property
    def rest_worked(self):
        return sum(1 for e in self.entries if e.replaces_rest)

    @property
    def people(self):
        """One line per person: what they will get, in the order of the sheet."""
        per = {}
        for e in self.entries:
            line = per.setdefault(e.employee.pk, {"employee": e.employee, "counts": Counter(), "rest_worked": 0})
            line["counts"][e.status] += 1
            line["rest_worked"] += 1 if e.replaces_rest else 0
        return [{"employee": v["employee"], "rest_worked": v["rest_worked"],
                 "counts": [(label, v["counts"][key]) for key, label in ENTRY.items() if v["counts"][key]]}
                for v in per.values()]

    @property
    def is_empty(self):
        return not self.entries and not self.rest


def _time(text):
    text = (text or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, "%H:%M").time()
    except ValueError:
        raise StaffSheetError(f"'{text}' is not a time. Use hours and minutes, like 08:30.") from None


def _aware(emp, d, ci, co):
    check_in = timezone.make_aware(datetime.combine(d, ci)) if ci else None
    check_out = None
    if co:
        out_date, shift = d, shift_on(emp, d)
        if shift and shift.crosses_midnight and co <= (ci or shift.start_time):
            out_date = d + timedelta(days=1)                          # a night shift ends the next morning
        check_out = timezone.make_aware(datetime.combine(out_date, co))
    return check_in, check_out


def make_plan(user, first, last, posted, fill="", period="week", **filters):
    """What would be created, what would be left out and why, and which rest days would be recorded alongside.
    Reads the database, writes nothing."""
    today = timezone.localdate()
    if first > today:
        raise StaffSheetError("Attendance cannot be recorded for a future date.")
    if fill and fill not in ENTRY:
        raise StaffSheetError("Choose Present, Absent or Half day to fill the empty days.")
    plan = Plan(first, last)
    for row in build(user, first, last, **filters).rows:
        emp = row.employee
        label = f"{emp.employee_no} {emp.full_name}"
        for cell in row.cells:
            if not cell.editable:
                continue
            value = posted.get(cell.key, "")
            value = value if value in ENTRY else ""
            if not value and not cell.rest_row and fill and cell.kind == "working":
                value = fill                                          # typed choices win; rest days are never filled
            if not value:
                if not cell.rest_row and cell.kind in OFF_DAYS:
                    plan.rest.append((emp, cell.date, cell.kind))     # catch-up: record the rest day as well
                continue
            check_in = check_out = None
            if period == "day" and value != Status.ABSENT.value:
                try:
                    check_in, check_out = _aware(emp, cell.date, _time(posted.get(f"i_{emp.pk}")),
                                                 _time(posted.get(f"o_{emp.pk}")))
                except StaffSheetError as exc:
                    plan.skipped.append(f"{label}, {cell.date:%d %b}: {exc}")
                    continue
            try:
                if cell.rest_row:
                    if check_in and check_out and check_out <= check_in:
                        raise services.AttendanceError("Check-out must be after check-in.")
                else:
                    services.validate_new_entry(emp, cell.date, check_in, check_out)
            except services.AttendanceError as exc:
                plan.skipped.append(f"{label}, {cell.date:%d %b}: {exc}")
                continue
            plan.entries.append(Entry(emp, cell.date, value, check_in, check_out, replaces_rest=cell.rest_row))
    return plan


def apply(plan):
    """Create what the plan says. Returns {'created': Counter, 'rest': Counter, 'worked_rest': int, 'skipped': [text]}.
    An entry that turns out to exist by now is skipped, never overwritten."""
    created, rest, skipped, worked_rest = Counter(), Counter(), list(plan.skipped), 0
    source = Attendance.Source.MANUAL if len(plan.entries) == 1 else Attendance.Source.BULK
    for e in plan.entries:
        try:
            if e.replaces_rest:
                restdays.replace_rest_day(e.employee, e.date, e.status, check_in=e.check_in, check_out=e.check_out)
                worked_rest += 1
            else:
                services.mark_attendance(e.employee, e.date, e.status, check_in=e.check_in, check_out=e.check_out,
                                         remarks="Entered by HR", source=source)
        except (services.AttendanceError, IntegrityError) as exc:
            skipped.append(f"{e.employee.employee_no} {e.employee.full_name}, {e.date:%d %b}: {exc}")
        else:
            created[e.status] += 1
    for emp, d, kind in plan.rest:
        if restdays.record_rest_day(emp, d, kind):
            rest[kind] += 1
    return {"created": created, "rest": rest, "worked_rest": worked_rest, "skipped": skipped}
