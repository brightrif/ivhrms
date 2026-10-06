from datetime import datetime, timedelta

from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.utils import timezone

from apps.audit import services as audit
from apps.core import approvals
from apps.core.models import ApprovalRequest
from apps.employees.models import Employee
from apps.employees.services import assignment_on
from apps.scheduling.services import HOLIDAY, WEEKLY_OFF, day_types, shift_on

from .models import Attendance, AttendanceCorrection

Status = Attendance.Status
Source = Attendance.Source
CORRECTION_FLOW = "attendance.correction"


class AttendanceError(Exception):
    pass


# ---------------------------------------------------------------- helpers

def _late_early(d, shift, check_in, check_out):
    if shift is None:
        return 0, 0
    start = timezone.make_aware(datetime.combine(d, shift.start_time))
    end = timezone.make_aware(datetime.combine(d + timedelta(days=1 if shift.crosses_midnight else 0),
                                               shift.end_time))
    late = early = 0
    if check_in:
        mins = int((check_in - start).total_seconds() // 60)
        late = mins if mins > shift.grace_minutes else 0
    if check_out:
        early = max(int((end - check_out).total_seconds() // 60), 0)
    return late, early


def _derive(employee, d, status, check_in, check_out):
    """Everything computed from the date: shift, lateness, department/site snapshots."""
    shift = shift_on(employee, d)
    late = early = 0
    if status in Attendance.WORKED:
        late, early = _late_early(d, shift, check_in, check_out)
        if status == Status.PRESENT:
            status = Status.LATE_ENTRY if late else (Status.EARLY_EXIT if early else status)
    rec = assignment_on(employee, d)
    return dict(status=status, shift=shift, late_minutes=late, early_exit_minutes=early,
                department_id=rec.department_id if rec else employee.department_id,
                location_id=rec.location_id if rec else employee.location_id)


def validate_new_entry(employee, d, check_in=None, check_out=None):
    if d > timezone.localdate():
        raise AttendanceError("Cannot record attendance for a future date.")
    if d < employee.joining_date:
        raise AttendanceError("That date is before the employee's joining date.")
    if check_in and check_out and check_out <= check_in:
        raise AttendanceError("Check-out must be after check-in.")
    if Attendance.objects.filter(employee=employee, date=d).exists():
        raise AttendanceError(f"An entry already exists for {d:%d %b %Y}. Submit a correction request to change it.")


# ---------------------------------------------------------------- entry

@transaction.atomic
def mark_attendance(employee, d, status, *, check_in=None, check_out=None, project=None, location=None,
                    remarks="", source=Source.MANUAL):
    validate_new_entry(employee, d, check_in, check_out)
    fields = _derive(employee, d, status, check_in, check_out)
    if location is not None:
        fields["location_id"] = location.pk
    return Attendance.objects.create(employee=employee, date=d, check_in=check_in, check_out=check_out,
                                     project=project, remarks=remarks, source=source, **fields)


def bulk_mark(employees, d, status, *, project=None, location=None, remarks=""):
    """Returns (created, skipped). Existing entries are never overwritten; they are reported as skipped."""
    created, skipped = [], []
    for emp in employees:
        try:
            created.append(mark_attendance(emp, d, status, project=project, location=location,
                                           remarks=remarks, source=Source.BULK))
        except AttendanceError as exc:
            skipped.append((emp, str(exc)))
    return created, skipped


def fill_non_working_days(company, d):
    """Create Holiday / Weekly Off rows for active employees with no entry on date d. Returns the count."""
    employees = (Employee.objects.filter(company=company, joining_date__lte=d,
                                         status__in=[Employee.Status.ACTIVE, Employee.Status.ON_NOTICE])
                 .exclude(attendance_records__date=d))
    count = 0
    for emp in employees:
        kind = day_types(emp, d, d)[d]
        if kind not in (HOLIDAY, WEEKLY_OFF):
            continue
        status = Status.HOLIDAY if kind == HOLIDAY else Status.WEEKLY_OFF
        Attendance.objects.create(employee=emp, date=d, source=Source.SYSTEM,
                                  **_derive(emp, d, status, None, None))
        count += 1
    return count


# ---------------------------------------------------------------- corrections (approval required)

@transaction.atomic
def request_correction(employee, d, requested_status, *, requested_by, reason, check_in=None, check_out=None,
                       project=None, location=None):
    if not reason.strip():
        raise AttendanceError("A reason is required.")
    if d > timezone.localdate():
        raise AttendanceError("Cannot correct a future date.")
    if check_in and check_out and check_out <= check_in:
        raise AttendanceError("Check-out must be after check-in.")
    if Attendance.objects.filter(employee=employee, date=d, is_locked=True).exists():
        raise AttendanceError("That date belongs to a closed payroll period.")
    if AttendanceCorrection.objects.filter(employee=employee, date=d,
                                           status=AttendanceCorrection.Status.PENDING).exists():
        raise AttendanceError("A correction for that date is already pending.")
    corr = AttendanceCorrection.objects.create(
        employee=employee, date=d, requested_status=requested_status, check_in=check_in, check_out=check_out,
        project=project, location=location, reason=reason)
    try:
        approvals.submit(CORRECTION_FLOW, corr, employee=employee, requested_by=requested_by)
    except approvals.ApprovalError as exc:
        raise AttendanceError(str(exc)) from exc
    return corr


@transaction.atomic
def finalize_correction(approval):
    corr = (AttendanceCorrection.objects.select_for_update().select_related("employee")
            .get(pk=approval.object_id))
    if corr.status != AttendanceCorrection.Status.PENDING:
        return corr
    if approval.status == ApprovalRequest.Status.APPROVED:
        _apply_correction(corr)
        corr.status = AttendanceCorrection.Status.APPROVED
    elif approval.status == ApprovalRequest.Status.REJECTED:
        corr.status = AttendanceCorrection.Status.REJECTED
    else:
        corr.status = AttendanceCorrection.Status.CANCELLED
    corr.save(update_fields=["status"])
    return corr


def _apply_correction(corr):
    emp, d = corr.employee, corr.date
    rec = Attendance.objects.select_for_update().filter(employee=emp, date=d).first()
    fields = _derive(emp, d, corr.requested_status, corr.check_in, corr.check_out)
    if corr.location_id:
        fields["location_id"] = corr.location_id
    note = f"Correction #{corr.pk}: {corr.reason}"[:255]
    if rec is None:
        Attendance.objects.create(employee=emp, date=d, check_in=corr.check_in, check_out=corr.check_out,
                                  project=corr.project, source=Source.CORRECTION, remarks=note, **fields)
        return
    if rec.is_locked:
        raise AttendanceError("That date belongs to a closed payroll period.")
    for key, value in fields.items():
        setattr(rec, key, value)
    rec.check_in, rec.check_out = corr.check_in, corr.check_out
    rec.project = corr.project or rec.project
    rec.leave_request, rec.source, rec.remarks = None, Source.CORRECTION, note
    rec.save()          # the audit module records the old and new values of every changed field


# ---------------------------------------------------------------- leave integration

def apply_leave(leave_request):
    lt, emp = leave_request.leave_type, leave_request.employee
    status = (Status.HALF_DAY if leave_request.is_half_day
              else Status.PAID_LEAVE if lt.is_paid else Status.UNPAID_LEAVE)
    replaceable = {Status.ABSENT, Status.HOLIDAY, Status.WEEKLY_OFF}
    conflicts = []
    for d, kind in day_types(emp, leave_request.start_date, leave_request.end_date).items():
        if (kind == HOLIDAY and lt.exclude_holidays) or (kind == WEEKLY_OFF and lt.exclude_weekly_offs):
            continue                                     # not a leave day, so the day keeps its own status
        fields = _derive(emp, d, status, None, None)
        rec = Attendance.objects.select_for_update().filter(employee=emp, date=d).first()
        if rec is None:
            Attendance.objects.create(employee=emp, date=d, source=Source.LEAVE, leave_request=leave_request,
                                      remarks=lt.name[:255], **fields)
        elif rec.status in replaceable and not rec.is_locked:
            for key, value in fields.items():
                setattr(rec, key, value)
            rec.source, rec.leave_request, rec.remarks = Source.LEAVE, leave_request, lt.name[:255]
            rec.save()
        else:
            conflicts.append(d)
    if conflicts:
        audit.log("update", leave_request, module="attendance", subject_employee_id=emp.pk,
                  company_id=emp.company_id,
                  reason="Approved leave overlaps existing attendance, left unchanged: "
                         + ", ".join(f"{d:%d %b}" for d in conflicts))


def remove_leave(leave_request):
    rows = Attendance.objects.filter(leave_request=leave_request)
    if rows.filter(is_locked=True).exists():
        raise AttendanceError("Some of these dates belong to a closed payroll period.")
    rows.delete()       # queryset delete still fires post_delete, so each removal is audited