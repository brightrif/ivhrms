"""Staff attendance: HR gives attendance for staff on a grid (a day, a week or a month at a time). Review, then
confirmation: nothing is created until the review is confirmed."""
from datetime import date
from urllib.parse import urlencode

from django.contrib import messages
from django.shortcuts import redirect, render
from django.utils import timezone

from apps.attendance import staffsheet
from apps.configuration import services as settings_service
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


def _default_period(user):
    """The period the sheet opens on: the Settings choice of the person's first company, else the week."""
    company = companies_for(user).order_by("name").first()
    try:
        value = settings_service.get("attendance.staff_sheet_period", company)
    except KeyError:                                           # the Settings tab has not been installed
        value = "week"
    return value if value in staffsheet.PERIODS else "week"


def _query(period, first, text, fill=""):
    params = {"period": period, "date": first.isoformat(), **{k: v for k, v in text.items() if v}}
    if fill:
        params["fill"] = fill
    return urlencode(params)


def _sheet(request, period, first, last, text, args, posted=None, fill=""):
    today = timezone.localdate()
    sheet = staffsheet.build(request.user, first, last, posted=posted, **args)
    nxt = staffsheet.shift_anchor(period, first, 1)
    return render(request, "web/attendance/staff_sheet.html", {
        "sheet": sheet, "period": period, "periods": staffsheet.PERIODS, "first": first, "last": last,
        "today": today, "fill": fill, "f": text, "size": f"gs-{period}", "ndays": len(sheet.days),
        "prev_date": staffsheet.shift_anchor(period, first, -1).isoformat(),
        "next_date": nxt.isoformat() if staffsheet.period_bounds(period, nxt)[0] <= today else None,
        "open_total": sum(r.open_days for r in sheet.rows),
        "entry": [(key, staffsheet.SHORT[key], label) for key, label in staffsheet.ENTRY.items()],
        "departments": Department.objects.filter(company__in=companies_for(request.user)).order_by("name"),
        "capped": len(sheet.rows) >= staffsheet.LIMIT})


@hr_perm("attendance.add_attendance")
def staff_attendance(request):
    today = timezone.localdate()
    source = request.POST if request.method == "POST" else request.GET
    period = source.get("period") if source.get("period") in staffsheet.PERIODS else _default_period(request.user)
    first, last = staffsheet.period_bounds(period, _day(source.get("date"), today))
    text, args = _filters(source)
    fill = source.get("fill", "")
    if first > today:                                          # a period that has not started: nothing to record
        if request.method == "POST":
            messages.error(request, "Attendance cannot be recorded for a future date. Nothing was saved.")
        else:
            messages.warning(request, "Attendance cannot be recorded for a future date. Showing the current period.")
        first, last = staffsheet.period_bounds(period, today)
        if request.method == "POST":
            return _sheet(request, period, first, last, text, args)
        return _sheet(request, period, first, last, text, args, fill=fill)

    if request.method != "POST":
        return _sheet(request, period, first, last, text, args, fill=fill)

    action = request.POST.get("action", "review")
    if action == "edit":                                       # back from the review, with the choices kept
        return _sheet(request, period, first, last, text, args, posted=request.POST, fill=fill)
    try:
        plan = staffsheet.make_plan(request.user, first, last, request.POST, fill=fill, period=period, **args)
    except staffsheet.StaffSheetError as exc:
        messages.error(request, str(exc))
        return _sheet(request, period, first, last, text, args, posted=request.POST, fill=fill)

    if action == "confirm":
        result = staffsheet.apply(plan)
        total = sum(result["created"].values())
        if total:
            parts = ", ".join(f"{n} {staffsheet.ENTRY[k].lower()}" for k, n in result["created"].items())
            messages.success(request, f"Attendance saved: {total} entr{'y' if total == 1 else 'ies'} ({parts}).")
        if result["worked_rest"]:
            messages.info(request, f"{result['worked_rest']} of them replaced a weekly off or holiday, because the person worked.")
        if sum(result["rest"].values()):
            names = {"weekly_off": "weekly offs", "holiday": "holidays"}
            messages.info(request, "Also recorded: " + ", ".join(f"{n} {names[k]}" for k, n in result["rest"].items()) + ".")
        for line in result["skipped"]:
            messages.warning(request, f"Not saved: {line}")
        if not total and not sum(result["rest"].values()) and not result["skipped"]:
            messages.info(request, "Nothing was saved.")
        return redirect(f"{request.path}?{_query(period, first, text)}")

    if plan.is_empty:                                          # review, but nothing chosen and no rest days to record
        if plan.skipped:
            for line in plan.skipped:
                messages.warning(request, f"Not saved: {line}")
        else:
            messages.warning(request, "Nothing to save yet. Choose Present, Absent or Half day for at least one day.")
        return _sheet(request, period, first, last, text, args, posted=request.POST, fill=fill)

    hidden = [(k, v) for k, v in request.POST.items() if k[:2] in ("c_", "i_", "o_") and v]
    return render(request, "web/attendance/staff_review.html", {
        "plan": plan, "hidden": hidden, "fill": fill, "f": text, "period": period, "periods": staffsheet.PERIODS,
        "first": first, "last": last})
