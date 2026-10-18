"""Staff attendance: HR gives attendance for staff. The sheet, then a review, then a confirmation: nothing is created
until the review is confirmed."""
from datetime import date, timedelta
from urllib.parse import urlencode

from django.contrib import messages
from django.shortcuts import redirect, render
from django.utils import timezone

from apps.attendance import staffsheet
from apps.organization.models import Department
from apps.organization.services import companies_for

from apps.web.access import hr_perm

FILTER_KEYS = ("q", "department", "employee")


def _day(raw, today):
    try:
        return date.fromisoformat(raw or "")
    except ValueError:
        return today


def _filters(data):
    """The filters in the form, as text for the page and as arguments for the service."""
    text = {k: (data.get(k) or "").strip() for k in FILTER_KEYS}
    args = {"q": text["q"],
            "department_id": int(text["department"]) if text["department"].isdigit() else None,
            "employee_id": int(text["employee"]) if text["employee"].isdigit() else None}
    return text, args


def _query(d, text, fill=""):
    params = {"date": d.isoformat(), **{k: v for k, v in text.items() if v}}
    if fill:
        params["fill"] = fill
    return urlencode(params)


def _sheet(request, d, text, args, posted=None, fill=""):
    today = timezone.localdate()
    rows = staffsheet.build(request.user, d, posted=posted, **args)
    return render(request, "web/attendance/staff_sheet.html", {
        "d": d, "rows": rows, "today": today, "fill": fill, "f": text,
        "prev_day": (d - timedelta(days=1)).isoformat(),
        "next_day": (d + timedelta(days=1)).isoformat() if d < today else None,
        "open_count": sum(1 for r in rows if r.open), "recorded_count": sum(1 for r in rows if not r.open),
        "statuses": list(staffsheet.ENTRY.items()),
        "departments": Department.objects.filter(company__in=companies_for(request.user)).order_by("name"),
        "capped": len(rows) >= staffsheet.LIMIT})


@hr_perm("attendance.add_attendance")
def staff_attendance(request):
    today = timezone.localdate()
    source = request.POST if request.method == "POST" else request.GET
    d = _day(source.get("date"), today)
    text, args = _filters(source)
    fill = source.get("fill", "")
    if d > today:
        if request.method == "POST":                           # never save anything for a day that was not asked for
            messages.error(request, "Attendance cannot be recorded for a future date. Nothing was saved.")
            return _sheet(request, today, text, args)
        messages.warning(request, "Attendance cannot be recorded for a future date. Showing today.")
        d = today

    if request.method != "POST":
        return _sheet(request, d, text, args, fill=fill)

    action = request.POST.get("action", "review")
    if action == "edit":                                       # back from the review, with the choices kept
        return _sheet(request, d, text, args, posted=request.POST, fill=fill)
    try:
        plan = staffsheet.make_plan(request.user, d, request.POST, fill=fill, **args)
    except staffsheet.StaffSheetError as exc:
        messages.error(request, str(exc))
        return _sheet(request, d, text, args, posted=request.POST, fill=fill)

    if action == "confirm":
        result = staffsheet.apply(plan)
        total = sum(result["created"].values())
        if total:
            parts = ", ".join(f"{n} {staffsheet.ENTRY[k].lower()}" for k, n in result["created"].items())
            messages.success(request, f"Attendance saved for {d:%a %d %b %Y}: {total} entr{'y' if total == 1 else 'ies'} ({parts}).")
        for line in result["skipped"]:
            messages.warning(request, f"Not saved: {line}")
        if not total and not result["skipped"]:
            messages.info(request, "Nothing was saved.")
        return redirect(f"{request.path}?{_query(d, text)}")

    if not plan.entries:                                       # review, but nothing chosen
        if plan.skipped:
            for line in plan.skipped:
                messages.warning(request, f"Not saved: {line}")
        else:
            messages.warning(request, "Nothing to save yet. Choose Present, Absent or Half day for at least one person.")
        return _sheet(request, d, text, args, posted=request.POST, fill=fill)

    hidden = [(k, v) for k, v in request.POST.items() if k[:2] in ("s_", "i_", "o_") and v]
    return render(request, "web/attendance/staff_review.html", {
        "d": d, "plan": plan, "hidden": hidden, "fill": fill, "f": text})
