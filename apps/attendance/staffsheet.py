"""Staff attendance entered by HR: one day, everyone on one page, and a review before anything is created.

Entries go through the normal attendance service, so every rule still applies: nothing in the future, nothing before
the joining date, check-out after check-in, and an entry that already exists is never overwritten (a change to it
needs a correction that the manager approves). The page builds a plan first; the plan is shown for confirmation and is
worked out again from the same form when it is confirmed, so nothing is trusted from the review page."""
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from django.db import IntegrityError
from django.db.models import Q
from django.utils import timezone

from apps.employees.models import Employee
from apps.scheduling.services import HOLIDAY, WEEKLY_OFF, day_types, shift_on

from . import services
from .models import Attendance

Status = Attendance.Status
ENTRY = {Status.PRESENT.value: "Present", Status.HALF_DAY.value: "Half day", Status.ABSENT.value: "Absent"}
OFF_DAYS = (HOLIDAY, WEEKLY_OFF)
LIMIT = 300


class StaffSheetError(Exception):
    pass


@dataclass
class Row:
    employee: object
    kind: str
    record: object = None
    status: str = ""                 # what is typed on the form (kept when the page comes back to be edited)
    check_in: str = ""
    check_out: str = ""

    @property
    def open(self):
        return self.record is None


@dataclass
class Entry:
    employee: object
    status: str
    check_in: object = None
    check_out: object = None

    @property
    def label(self):
        return ENTRY[self.status]


@dataclass
class Plan:
    date: object
    entries: list = field(default_factory=list)
    skipped: list = field(default_factory=list)

    @property
    def counts(self):
        counted = Counter(e.status for e in self.entries)
        return [(label, counted[key]) for key, label in ENTRY.items() if counted[key]]


def staff_for(user, d, q="", department_id=None, employee_id=None):
    """Staff (never labour) who had joined by this day, are still with the company, and match the filters."""
    qs = (Employee.objects.for_user(user).exclude(status=Employee.Status.SEPARATED)
          .exclude(worker_type=Employee.WorkerType.LABOR).filter(joining_date__lte=d)
          .select_related("department", "designation"))
    if employee_id:
        qs = qs.filter(pk=employee_id)
    if department_id:
        qs = qs.filter(department_id=department_id)
    for word in (q or "").split():
        qs = qs.filter(Q(first_name__icontains=word) | Q(last_name__icontains=word)
                       | Q(employee_no__icontains=word))
    return list(qs.order_by("employee_no")[:LIMIT])


def build(user, d, posted=None, **filters):
    """The rows of the sheet for one day. `posted` refills what was typed when the page comes back."""
    staff = staff_for(user, d, **filters)
    records = {a.employee_id: a for a in Attendance.objects.filter(date=d, employee_id__in=[e.pk for e in staff])}
    rows = []
    for emp in staff:
        row = Row(emp, day_types(emp, d, d)[d], records.get(emp.pk))
        if posted is not None and row.open:
            row.status = posted.get(f"s_{emp.pk}", "") if posted.get(f"s_{emp.pk}", "") in ENTRY else ""
            row.check_in, row.check_out = posted.get(f"i_{emp.pk}", ""), posted.get(f"o_{emp.pk}", "")
        rows.append(row)
    return rows


def _time(text):
    text = (text or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, "%H:%M").time()
    except ValueError:
        raise StaffSheetError(f"'{text}' is not a time. Use hours and minutes, like 08:30.")


def _aware(emp, d, ci, co):
    check_in = timezone.make_aware(datetime.combine(d, ci)) if ci else None
    check_out = None
    if co:
        out_date, shift = d, shift_on(emp, d)
        if shift and shift.crosses_midnight and co <= (ci or shift.start_time):
            out_date = d + timedelta(days=1)                          # a night shift ends the next morning
        check_out = timezone.make_aware(datetime.combine(out_date, co))
    return check_in, check_out


def make_plan(user, d, posted, fill="", **filters):
    """What would be created, and what would be left out and why. Reads the database, writes nothing."""
    if d > timezone.localdate():
        raise StaffSheetError("Attendance cannot be recorded for a future date.")
    if fill and fill not in ENTRY:
        raise StaffSheetError("Choose Present, Absent or Half day to fill the empty days.")
    plan = Plan(date=d)
    for row in build(user, d, **filters):
        if not row.open:
            continue                                                   # already recorded: never changed here
        emp = row.employee
        value = posted.get(f"s_{emp.pk}", "")
        value = value if value in ENTRY else ""
        if not value and fill and row.kind not in OFF_DAYS:
            value = fill                                              # typed choices win; rest days are not filled
        if not value:
            continue
        label = f"{emp.employee_no} {emp.full_name}"
        check_in = check_out = None
        if value != Status.ABSENT.value:
            try:
                check_in, check_out = _aware(emp, d, _time(posted.get(f"i_{emp.pk}")), _time(posted.get(f"o_{emp.pk}")))
            except StaffSheetError as exc:
                plan.skipped.append(f"{label}: {exc}")
                continue
        try:
            services.validate_new_entry(emp, d, check_in, check_out)
        except services.AttendanceError as exc:
            plan.skipped.append(f"{label}: {exc}")
            continue
        plan.entries.append(Entry(emp, value, check_in, check_out))
    return plan


def apply(plan):
    """Create what the plan says. Returns {'created': Counter, 'skipped': [text]}; an entry that turns out to exist
    by now is skipped, never overwritten."""
    created, skipped = Counter(), list(plan.skipped)
    source = Attendance.Source.MANUAL if len(plan.entries) == 1 else Attendance.Source.BULK
    for e in plan.entries:
        try:
            services.mark_attendance(e.employee, plan.date, e.status, check_in=e.check_in, check_out=e.check_out,
                                     remarks="Entered by HR", source=source)
        except (services.AttendanceError, IntegrityError) as exc:
            skipped.append(f"{e.employee.employee_no} {e.employee.full_name}: {exc}")
        else:
            created[e.status] += 1
    return {"created": created, "skipped": skipped}
