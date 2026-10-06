from datetime import timedelta

from django.db import transaction
from django.db.models import Q

from .models import Holiday, ShiftAssignment, default_weekly_off

HOLIDAY, WEEKLY_OFF, WORKING = "holiday", "weekly_off", "working"


class SchedulingError(Exception):
    pass


def holidays_between(company_id, start, end):
    """{date: Holiday}. A company-specific holiday wins over a global one."""
    rows = (Holiday.objects.filter(date__range=(start, end), is_active=True)
            .filter(Q(company_id=company_id) | Q(company__isnull=True)))
    found = {}
    for h in rows:
        if h.date not in found or h.company_id is not None:
            found[h.date] = h
    return found


def day_types(employee, start, end):
    """{date: 'holiday' | 'weekly_off' | 'working'} for the employee, using the shift in force each day."""
    holidays = holidays_between(employee.company_id, start, end)
    assignments = list(
        ShiftAssignment.objects.filter(employee=employee, effective_from__lte=end)
        .filter(Q(effective_to__isnull=True) | Q(effective_to__gte=start))
        .select_related("shift"))
    default_off = default_weekly_off()
    result, d = {}, start
    while d <= end:
        if d in holidays:
            result[d] = HOLIDAY
        else:
            off = default_off
            for a in assignments:
                if a.effective_from <= d and (a.effective_to is None or d <= a.effective_to):
                    off = a.shift.weekly_off_days
                    break
            result[d] = WEEKLY_OFF if d.weekday() in off else WORKING
        d += timedelta(days=1)
    return result


def shift_on(employee, d):
    a = (ShiftAssignment.objects.filter(employee=employee, effective_from__lte=d)
         .filter(Q(effective_to__isnull=True) | Q(effective_to__gte=d))
         .select_related("shift").first())
    return a.shift if a else None


def check_assignable(employee, shift, effective_from):
    if shift.company_id != employee.company_id:
        raise SchedulingError("The shift belongs to a different company.")
    if not shift.is_active:
        raise SchedulingError("The shift is inactive.")
    if ShiftAssignment.objects.filter(employee=employee, effective_from__gte=effective_from).exists():
        raise SchedulingError("A shift assignment already starts on or after that date.")


@transaction.atomic
def assign_shift(employee, shift, effective_from):
    check_assignable(employee, shift, effective_from)
    current = ShiftAssignment.objects.filter(employee=employee, effective_to__isnull=True).first()
    if current:
        current.effective_to = effective_from - timedelta(days=1)
        current.save()
    return ShiftAssignment.objects.create(employee=employee, shift=shift, effective_from=effective_from)


def assign_shift_bulk(employees, shift, effective_from):
    """Assign a shift to a group (e.g. a department, project or site queryset). Returns (done, skipped)."""
    done, skipped = [], []
    for emp in employees:
        try:
            done.append(assign_shift(emp, shift, effective_from))
        except SchedulingError as exc:
            skipped.append((emp, str(exc)))
    return done, skipped