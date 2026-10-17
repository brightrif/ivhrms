"""Changing an employee's joining date.

The joining date is also where the first assignment record, the first shift and the first pay line begin, so those move
with it. A later date is refused when anything else is already recorded before it. Leave balances are not touched: the
entitlement for the joining year was worked out from the old date, so HR checks it afterwards."""
from django.db import transaction
from django.db.models import Min
from django.utils import timezone

from .models import Employee


class JoiningDateError(Exception):
    """The new date cannot be used. The message is written to be shown to the person."""


def _earliest(qs, field):
    return qs.aggregate(first=Min(field))["first"]


def _moves_with_the_date(employee, old):
    """The records that begin on the old joining date, and so move with it."""
    from apps.labor.models import LaborRate
    from apps.scheduling.models import ShiftAssignment

    rows = list(employee.history.filter(effective_from=old))
    rows += list(ShiftAssignment.objects.filter(employee=employee, effective_from=old))
    rows += list(LaborRate.objects.filter(profile__employee=employee, effective_from=old))
    return rows


def _stay_put(employee, old):
    """(label, earliest date, strict) for everything that does not move. A strict one cannot share the new date."""
    from apps.attendance.models import Attendance
    from apps.labor.allocation import LaborAllocation
    from apps.labor.models import LaborRate
    from apps.labor.timesheet import TimeEntry
    from apps.leave.models import LeaveRequest
    from apps.scheduling.models import ShiftAssignment

    return [
        ("An assignment change", _earliest(employee.history.exclude(effective_from=old), "effective_from"), True),
        ("A shift change", _earliest(ShiftAssignment.objects.filter(employee=employee).exclude(effective_from=old),
                                     "effective_from"), True),
        ("A pay change", _earliest(LaborRate.objects.filter(profile__employee=employee).exclude(effective_from=old),
                                   "effective_from"), True),
        ("A site allocation", _earliest(LaborAllocation.objects.filter(employee=employee), "effective_from"), False),
        ("Site hours", _earliest(TimeEntry.objects.filter(employee=employee), "date"), False),
        ("Attendance", _earliest(Attendance.objects.filter(employee=employee), "date"), False),
        ("Leave", _earliest(LeaveRequest.objects.filter(employee=employee).exclude(status="cancelled"),
                            "start_date"), False),
    ]


@transaction.atomic
def change_joining_date(employee, new_date):
    """Move the joining date. Returns notes to show the person (things HR should look at afterwards)."""
    employee = Employee.objects.select_for_update().get(pk=employee.pk)
    old = employee.joining_date
    if new_date == old:
        return []
    if employee.status == Employee.Status.SEPARATED:
        raise JoiningDateError(f"{employee.full_name} has left the company.")
    if new_date > timezone.localdate():
        raise JoiningDateError("The joining date cannot be in the future.")
    if new_date > old:
        for label, first, strict in _stay_put(employee, old):
            if first is not None and (first < new_date or (strict and first == new_date)):
                rule = "must be before that" if strict else "cannot be after that"
                raise JoiningDateError(f"{label} is recorded on {first:%d %b %Y}. The joining date {rule}.")
    for row in _moves_with_the_date(employee, old):
        row.effective_from = new_date
        row.save()
    employee.joining_date = new_date
    employee.save()

    notes = []
    from apps.leave.models import LeaveLedger
    if LeaveLedger.objects.filter(employee=employee, kind="entitlement").exists():
        notes.append("Their leave entitlement was worked out from the old joining date. "
                     "Check the leave balance and adjust it if needed.")
    return notes