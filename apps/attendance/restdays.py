"""Weekly offs and holidays as attendance rows.

A rest day is recorded for everyone who has no entry that day, so payroll and the calendars see it as a paid day. The
system writes these rows (source "system"). If someone actually worked on a rest day, HR can replace the system's row
with Present or Half day straight away; a row that a person entered still needs a correction that the manager approves."""
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.scheduling.services import HOLIDAY

from . import services
from .models import Attendance

Status, Source = Attendance.Status, Attendance.Source
REST_STATUSES = (Status.HOLIDAY, Status.WEEKLY_OFF)


def record_rest_day(employee, d, kind):
    """Create the weekly-off or holiday row for a day with no entry. Returns the row, or None if the day already has one."""
    status = Status.HOLIDAY if kind == HOLIDAY else Status.WEEKLY_OFF
    if Attendance.objects.filter(employee=employee, date=d).exists():
        return None
    try:
        with transaction.atomic():
            return Attendance.objects.create(employee=employee, date=d, source=Source.SYSTEM,
                                             **services._derive(employee, d, status, None, None))
    except IntegrityError:
        return None


@transaction.atomic
def replace_rest_day(employee, d, status, *, check_in=None, check_out=None):
    """Someone worked on their rest day: turn the system's weekly-off or holiday row into Present, Half day or Absent."""
    rec = Attendance.objects.select_for_update().filter(employee=employee, date=d).first()
    if rec is None:
        raise services.AttendanceError("Nothing is recorded for that day.")
    if rec.source != Source.SYSTEM or rec.status not in REST_STATUSES:
        raise services.AttendanceError("Only a weekly off or holiday recorded by the system can be changed here. "
                                       "Use a correction for anything else.")
    if rec.is_locked:
        raise services.AttendanceError("That date belongs to a closed payroll period.")
    if d > timezone.localdate():
        raise services.AttendanceError("Cannot record attendance for a future date.")
    if check_in and check_out and check_out <= check_in:
        raise services.AttendanceError("Check-out must be after check-in.")
    for key, value in services._derive(employee, d, status, check_in, check_out).items():
        setattr(rec, key, value)
    rec.check_in, rec.check_out = check_in, check_out
    rec.source, rec.remarks = Source.MANUAL, "Worked on a rest day. Entered by HR"
    rec.save()                                   # the audit module records the old and the new values
    return rec
