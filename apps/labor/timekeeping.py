"""The site timesheet: hours per worker per day, entered by HR beside the attendance sheet.

Hours can only be entered for a day the worker actually worked at that site (the attendance sheet comes first).
Each entry is charged to the project, site and work order the worker was allocated to that day, and remembers the
day's expected hours and overtime eligibility, so later changes to pay terms never alter an old timesheet.
Entries stay editable drafts until a period is confirmed for the site; confirmed entries are locked."""
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from apps.attendance.models import Attendance

from . import sitesheet
from .allocation import LaborAllocation
from .models import LaborRate
from .overtime import OvertimePolicy
from .services import LaborError
from .timesheet import TimeEntry

ZERO, MIN_HOURS, MAX_HOURS = Decimal("0"), Decimal("0.25"), Decimal("24")


@dataclass
class TCell:
    date: date
    kind: str
    attendance: Attendance = None
    entry: TimeEntry = None
    covered: bool = False
    expected: Decimal = None            # hours this day expects, when the worker worked and has pay terms
    eligible: bool = True
    work_order_id: int = None
    can_enter: bool = False
    key: str = ""
    ot_key: str = ""                    # the overtime box, shown for workers on several projects
    main: bool = True                   # this project is the worker's main one that day
    others: list = field(default_factory=list)       # the worker's hours on other projects that day

    @property
    def worked(self):
        return self.attendance is not None and self.attendance.status in Attendance.WORKED

    @property
    def locked(self):
        return self.entry is not None and self.entry.is_confirmed

    @property
    def letter(self):
        return sitesheet.SHORT.get(self.attendance.status, "?") if self.attendance else ""

    @property
    def other_hours(self):
        return sum((e.hours for e in self.others), ZERO)

    @property
    def other_regular(self):
        """Regular hours already given to other projects that day."""
        return sum((e.regular_hours for e in self.others), ZERO)

    @property
    def other_projects(self):
        return ", ".join(sorted({e.project.code for e in self.others}))

    @property
    def is_missing(self):
        """A worked day nobody has put hours on. Hours on another project are enough: that project has the day."""
        return self.covered and self.worked and self.entry is None and not self.others and self.expected is not None

    @property
    def is_conflict(self):
        """Hours exist for a day that was not worked (the attendance was changed afterwards)."""
        return self.entry is not None and not self.worked


@dataclass
class TRow:
    employee: object
    profile: object
    cells: list = field(default_factory=list)
    split: bool = False                 # works for more than one project in this period: overtime box and other hours

    def _sum(self, attr):
        return sum((getattr(c.entry, attr) for c in self.cells if c.entry), ZERO)

    hours = property(lambda self: self._sum("hours"))
    regular = property(lambda self: self._sum("regular_hours"))
    overtime = property(lambda self: self._sum("overtime_hours"))
    missing = property(lambda self: sum(1 for c in self.cells if c.is_missing))
    conflicts = property(lambda self: sum(1 for c in self.cells if c.is_conflict))


@dataclass
class TSheet:
    project: object
    location: object
    first: date
    last: date
    days: list
    rows: list
    overtime_mode: str = "applies"        # "applies" | "none" | "undecided", for the company on the last day shown

    @property
    def drafts(self):
        return sum(1 for r in self.rows for c in r.cells if c.entry and not c.entry.is_confirmed)

    @property
    def confirmed(self):
        return sum(1 for r in self.rows for c in r.cells if c.locked)

    missing = property(lambda self: sum(r.missing for r in self.rows))
    conflicts = property(lambda self: sum(r.conflicts for r in self.rows))
    hours = property(lambda self: sum((r.hours for r in self.rows), ZERO))
    overtime = property(lambda self: sum((r.overtime for r in self.rows), ZERO))


def parse_hours(text):
    """'8', '7.5', '10.25' -> Decimal. Raises LaborError with a message fit to show."""
    try:
        value = Decimal(str(text).strip())
    except InvalidOperation:
        raise LaborError(f"'{text}' is not a number of hours.") from None
    if not value.is_finite() or value != value.quantize(Decimal("0.01")):
        raise LaborError(f"'{text}': use at most two decimals, for example 7.5.")
    if not MIN_HOURS <= value <= MAX_HOURS:
        raise LaborError(f"{value}: enter between 0.25 and 24 hours, or leave it blank.")
    return value


def build_timesheet(user, project, location, first, last, today=None):
    today = today or timezone.localdate()
    base = sitesheet.build_sheet(user, project, location, first, last, today)
    ids = [r.employee.pk for r in base.rows]
    entries, elsewhere = {}, defaultdict(list)
    for e in TimeEntry.objects.filter(employee_id__in=ids, date__range=(first, last)).select_related("project"):
        if e.project_id == project.pk and e.location_id == location.pk:
            entries[(e.employee_id, e.date)] = e
        else:
            elsewhere[(e.employee_id, e.date)].append(e)
    # workers who are on, or have hours on, another project in this period get the overtime box and the other hours
    several = {employee_id for employee_id, _day in elsewhere}
    several |= set(LaborAllocation.objects.filter(employee_id__in=ids, effective_from__lte=last)
                   .filter(Q(effective_to__isnull=True) | Q(effective_to__gte=first))
                   .exclude(project=project).values_list("employee_id", flat=True))
    several |= {r.employee.pk for r in base.rows if getattr(r.profile, "serves_all_projects", False)}
    rates = defaultdict(list)
    for rate in LaborRate.objects.filter(profile__employee_id__in=ids).select_related("profile"):
        rates[rate.profile.employee_id].append(rate)
    spans = defaultdict(list)
    for a in (LaborAllocation.objects.filter(employee_id__in=ids, project=project, location=location,
                                             effective_from__lte=last)
              .filter(Q(effective_to__isnull=True) | Q(effective_to__gte=first))):
        spans[a.employee_id].append(a)

    def rate_on(employee_id, d):
        for r in rates[employee_id]:
            if r.effective_from <= d and (r.effective_to is None or d <= r.effective_to):
                return r
        return None

    policies = list(OvertimePolicy.objects.filter(company_id=project.company_id))

    def pays_overtime(d):
        """What the company's rules say on a day. With no rules set yet the worker's own setting decides."""
        policy = next((p for p in policies if p.effective_from <= d and (p.effective_to is None or d <= p.effective_to)), None)
        return True if policy is None else policy.overtime_applies

    last_policy = next((p for p in policies if p.effective_from <= last and (p.effective_to is None or last <= p.effective_to)), None)
    mode = "undecided" if last_policy is None else ("applies" if last_policy.overtime_applies else "none")
    rows = []
    for brow in base.rows:
        employee = brow.employee
        row = TRow(employee=employee, profile=brow.profile, split=employee.pk in several)
        for c in brow.cells:
            entry = entries.get((employee.pk, c.date))
            span = next((a for a in spans[employee.pk] if a.effective_from <= c.date
                         and (a.effective_to is None or c.date <= a.effective_to)), None)
            cell = TCell(date=c.date, kind=c.kind, attendance=c.record, entry=entry, covered=c.covered,
                         work_order_id=span.work_order_id if span else None, key=f"h_{employee.pk}_{c.date:%Y%m%d}",
                         ot_key=f"o_{employee.pk}_{c.date:%Y%m%d}",
                         main=(span is not None and span.is_main) if row.split else True,
                         others=elsewhere.get((employee.pk, c.date), []))
            rate = rate_on(employee.pk, c.date)
            if cell.worked and rate is not None:
                cell.expected = (rate.standard_hours * Decimal(str(Attendance.DAY_FRACTION[c.record.status]))
                                 ).quantize(Decimal("0.01"))
                cell.eligible = rate.overtime_eligible and pays_overtime(c.date)
            cell.can_enter = (cell.covered and cell.worked and cell.expected is not None and not cell.locked
                              and c.date <= today)
            row.cells.append(cell)
        rows.append(row)
    return TSheet(project=project, location=location, first=first, last=last, days=base.days, rows=rows,
                  overtime_mode=mode)


def save_hours(user, project, location, first, last, posted, fill=""):
    """Save the hours typed on the sheet. A blank box on an existing draft clears it. With fill="standard", every
    empty box on a worked day gets that day's expected hours, so HR types only the exceptions and overtime.
    A worker on several projects also has an overtime box: the overtime this project asked for. Filling the standard
    day goes only to the worker's main project, and only when no other project has hours that day.
    Returns {"created": n, "updated": n, "cleared": n, "skipped": [text]}."""
    if fill not in ("", "standard"):
        raise LaborError("Choose what to fill empty days with, or leave it blank.")
    sheet = build_timesheet(user, project, location, first, last)
    done, skipped = Counter(), []
    with transaction.atomic():
        for row in sheet.rows:
            for cell in row.cells:
                if not cell.can_enter:
                    continue
                label = f"{row.employee.employee_no} {cell.date:%d %b}"
                raw = posted.get(cell.key)
                raw_ot = posted.get(cell.ot_key) if row.split else None
                try:
                    if cell.entry is not None:                            # an existing draft
                        if raw is None:
                            continue
                        if not raw.strip():
                            cell.entry.delete()
                            done["cleared"] += 1
                            continue
                        hours = parse_hours(raw)
                        if not row.split:
                            if hours != cell.entry.hours:
                                cell.entry.hours = hours
                                cell.entry.save(update_fields=["hours"])
                                done["updated"] += 1
                            continue
                        regular = _regular_for(cell, hours, raw_ot)
                        if hours != cell.entry.hours or regular != cell.entry.expected_hours:
                            cell.entry.hours, cell.entry.expected_hours = hours, regular
                            cell.entry.save(update_fields=["hours", "expected_hours"])
                            done["updated"] += 1
                        continue
                    text = (raw or "").strip()
                    if not text and fill == "standard":
                        if row.split and not (cell.main and not cell.others):
                            continue
                        hours = cell.expected
                    elif text:
                        hours = parse_hours(text)
                    else:
                        continue
                    regular = _regular_for(cell, hours, raw_ot) if row.split else cell.expected
                    TimeEntry.objects.create(
                        employee=row.employee, date=cell.date, project=project, location=location,
                        work_order_id=cell.work_order_id, hours=hours, expected_hours=regular,
                        overtime_eligible=cell.eligible)
                    done["created"] += 1
                except (LaborError, IntegrityError) as exc:
                    skipped.append(f"{label}: {exc}")
    return {"created": done["created"], "updated": done["updated"], "cleared": done["cleared"], "skipped": skipped}


def _period_entries(user, sheet, status):
    return TimeEntry.objects.for_user(user).filter(
        project=sheet.project, location=sheet.location, date__range=(sheet.first, sheet.last), status=status,
        employee_id__in=[r.employee.pk for r in sheet.rows])


@transaction.atomic
def confirm_period(user, project, location, first, last):
    """Lock the period's hours for this site. Everyone who worked must have hours first."""
    sheet = build_timesheet(user, project, location, first, last)
    if sheet.missing:
        raise LaborError(f"{sheet.missing} worked day{'s' if sheet.missing != 1 else ''} still "
                         f"{'have' if sheet.missing != 1 else 'has'} no hours. Fill them in (or fill the standard hours) first.")
    if sheet.conflicts:
        raise LaborError(f"{sheet.conflicts} entr{'ies are' if sheet.conflicts != 1 else 'y is'} on a day that is not marked "
                         "as worked in attendance. Clear them or fix the attendance first.")
    entries = list(_period_entries(user, sheet, TimeEntry.Status.DRAFT))
    if not entries:
        raise LaborError("There are no draft hours to confirm in this period.")
    for e in entries:                       # one save each, so the audit log records every change
        e.status = TimeEntry.Status.CONFIRMED
        e.save(update_fields=["status"])
    return len(entries)


@transaction.atomic
def reopen_period(user, project, location, first, last):
    """Unlock a confirmed period so the hours can be corrected."""
    sheet = build_timesheet(user, project, location, first, last)
    entries = list(_period_entries(user, sheet, TimeEntry.Status.CONFIRMED))
    if not entries:
        raise LaborError("Nothing is confirmed in this period.")
    from .overtime import OvertimeClaim                  # imported here: overtime builds on timesheets
    claims = OvertimeClaim.objects.filter(entry__in=entries)
    decided = claims.exclude(status=OvertimeClaim.Status.PENDING).count()
    if decided:
        raise LaborError(f"{decided} overtime claim{'s have' if decided != 1 else ' has'} already been decided for "
                         "these hours. Void the overtime claims first (under Overtime), then reopen.")
    for claim in claims:                                 # pending ones are worked out again from the corrected hours
        claim.delete()
    for e in entries:
        e.status = TimeEntry.Status.DRAFT
        e.save(update_fields=["status"])
    return len(entries)


def _fmt(value):
    text = f"{value:f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def parse_overtime(text, hours):
    """The overtime inside a line of `hours`: 0 up to all of them. Raises LaborError with a message fit to show."""
    try:
        value = Decimal(str(text).strip())
    except InvalidOperation:
        raise LaborError(f"'{text}' is not a number of overtime hours.") from None
    if not value.is_finite() or value != value.quantize(Decimal("0.01")):
        raise LaborError(f"'{text}': use at most two decimals for the overtime hours.")
    if not ZERO <= value <= hours:
        raise LaborError(f"{_fmt(value)}: the overtime must be between 0 and the {_fmt(hours)} hours entered.")
    return value


def _regular_for(cell, hours, ot_raw):
    """The regular part of a line's hours: what goes in expected_hours. The rest is overtime this project asked for.

    One project that day and no overtime typed: the standard day, as it has always been (anything above is overtime).
    Otherwise the regular hours are the hours less the typed overtime, and the regular hours of all the day's lines
    together may not pass the day's standard."""
    text = (ot_raw or "").strip()
    day = cell.expected
    if text:
        if not cell.eligible:
            raise LaborError("this worker is not eligible for overtime that day, so leave the overtime box empty.")
        regular = hours - parse_overtime(text, hours)
    elif cell.others:
        if not cell.eligible:
            return hours
        regular = hours
    else:
        return day
    total = cell.other_regular + regular
    if cell.eligible and total > day:
        raise LaborError(f"regular hours that day would be {_fmt(total)}, more than the {_fmt(day)} a day allows. "
                         "Put the extra into overtime on the project that asked for it.")
    return regular
