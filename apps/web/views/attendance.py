"""My attendance: the monthly view and correction requests."""

from calendar import monthrange
from collections import Counter
from datetime import date, datetime, timedelta

from django.http import Http404
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from apps.attendance import services as att_services
from apps.attendance.models import Attendance, AttendanceCorrection
from apps.scheduling.services import day_types, shift_on
from apps.web.access import employee_required
from apps.web.forms.attendance import CorrectionForm


def _parse_month(value, today):
    try:
        year, month = (int(x) for x in value.split("-"))
        return date(year, month, 1)
    except (ValueError, AttributeError):
        return today.replace(day=1)


def _parse_day(value):
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise Http404


def _day_rows(emp, first, last):
    today = timezone.localdate()
    records = {a.date: a for a in Attendance.objects.filter(employee=emp, date__range=(first, last))}
    kinds = day_types(emp, first, last)
    pending = set(AttendanceCorrection.objects
                  .filter(employee=emp, date__range=(first, last), status=AttendanceCorrection.Status.PENDING)
                  .values_list("date", flat=True))
    rows, d = [], first
    while d <= last:
        rec = records.get(d)
        rows.append({"date": d, "rec": rec, "kind": kinds[d], "future": d > today, "pending": d in pending,
                     "can_correct": d <= today and d not in pending and not (rec and rec.is_locked)})
        d += timedelta(days=1)
    return rows


@employee_required
def attendance(request):
    emp, today = request.employee, timezone.localdate()
    first = _parse_month(request.GET.get("month"), today)
    last = first.replace(day=monthrange(first.year, first.month)[1])
    rows = _day_rows(emp, first, last)
    counts = Counter(r["rec"].status for r in rows if r["rec"])
    summary = {"worked": counts["present"] + counts["late_entry"] + counts["early_exit"],
               "half": counts["half_day"], "absent": counts["absent"],
               "leave": counts["paid_leave"] + counts["unpaid_leave"], "late": counts["late_entry"]}
    nxt = last + timedelta(days=1)
    return render(request, "web/attendance/month.html", {
        "rows": rows, "summary": summary, "month": first,
        "prev_month": (first - timedelta(days=1)).strftime("%Y-%m"),
        "next_month": nxt.strftime("%Y-%m") if nxt <= today else None,
    })


@employee_required
def attendance_row(request, day):
    d = _parse_day(day)
    return render(request, "web/attendance/_row.html", {"r": _day_rows(request.employee, d, d)[0]})


@employee_required
@require_http_methods(["GET", "POST"])
def attendance_correct(request, day):
    emp, d = request.employee, _parse_day(day)
    ctx = {"iso": d.isoformat()}
    if request.method == "GET":
        return render(request, "web/attendance/_correction_form.html", {**ctx, "form": CorrectionForm()})

    form = CorrectionForm(request.POST)
    if form.is_valid():
        cd = form.cleaned_data
        if d < emp.joining_date:
            form.add_error(None, "That date is before your joining date.")
        else:
            check_in = timezone.make_aware(datetime.combine(d, cd["check_in"])) if cd["check_in"] else None
            check_out = None
            if cd["check_out"]:
                out_date, shift = d, shift_on(emp, d)
                if shift and shift.crosses_midnight and cd["check_out"] <= (cd["check_in"] or shift.start_time):
                    out_date = d + timedelta(days=1)             # night shift ends the next morning
                check_out = timezone.make_aware(datetime.combine(out_date, cd["check_out"]))
            try:
                att_services.request_correction(emp, d, cd["status"], requested_by=request.user,
                                                reason=cd["reason"], check_in=check_in, check_out=check_out)
            except att_services.AttendanceError as exc:
                form.add_error(None, str(exc))
            else:
                resp = render(request, "web/attendance/_row.html", {"r": _day_rows(emp, d, d)[0]})
                resp["HX-Retarget"] = f"#att-row-{d.isoformat()}"     # replace the whole row, not the form
                resp["HX-Reswap"] = "outerHTML"
                return resp
    return render(request, "web/attendance/_correction_form.html", {**ctx, "form": form})
