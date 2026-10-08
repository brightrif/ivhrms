from datetime import date
from urllib.parse import urlencode

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone

from apps.configuration import services as settings_service
from apps.labor import sitesheet, timekeeping
from apps.labor.services import LaborError

from apps.web.access import hr_perm

# what each button needs. Saving hours and confirming are everyday HR work; reopening a confirmed period is rarer
# and needs the delete permission, which no group has by default.
ACTION_PERMS = {"save": "labor.add_timeentry", "confirm": "labor.change_timeentry", "reopen": "labor.delete_timeentry"}


def _anchor(value, today):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return today


@hr_perm("labor.view_timeentry")
def labor_timesheet(request):
    """HR's site timesheet: hours per worker per day for a day, a week or a month at one site."""
    today = timezone.localdate()
    data = request.POST if request.method == "POST" else request.GET
    period = data.get("period") if data.get("period") in sitesheet.PERIODS else settings_service.get("labor.sheet_period")
    anchor = min(_anchor(data.get("date"), today), today)
    first, last = sitesheet.period_bounds(period, anchor)
    sites = sitesheet.sites_in_period(request.user, first, last)
    chosen = next((s for s in sites if f"{s[0].pk}:{s[1].pk}" == data.get("site")), sites[0] if sites else None)
    here = {"period": period, "date": anchor.isoformat(), **({"site": f"{chosen[0].pk}:{chosen[1].pk}"} if chosen else {})}

    if request.method == "POST":
        action = request.POST.get("action", "save")
        if action not in ACTION_PERMS or not request.user.has_perm(ACTION_PERMS[action]):
            raise PermissionDenied
        if chosen is None:
            messages.error(request, "There is no site to work on in this period.")
        else:
            project, location = chosen
            place = f"{project.code} / {location.name}"
            try:
                if action == "save":
                    r = timekeeping.save_hours(request.user, project, location, first, last, request.POST,
                                               fill=request.POST.get("fill", ""))
                    parts = [f"{r[k]} {word}" for k, word in (("created", "added"), ("updated", "changed"),
                                                             ("cleared", "cleared")) if r[k]]
                    if parts:
                        messages.success(request, "Hours saved: " + ", ".join(parts) + ".")
                    else:
                        messages.info(request, "Nothing to save.")
                    if r["skipped"]:
                        messages.warning(request, "Not saved: " + " | ".join(r["skipped"][:5]))
                elif action == "confirm":
                    n = timekeeping.confirm_period(request.user, project, location, first, last)
                    messages.success(request, f"Confirmed {n} entr{'ies' if n != 1 else 'y'} for {place}. They are now locked.")
                else:
                    n = timekeeping.reopen_period(request.user, project, location, first, last)
                    messages.success(request, f"Reopened {n} entr{'ies' if n != 1 else 'y'} for {place}.")
            except LaborError as exc:
                messages.error(request, str(exc))
        return redirect(f"{reverse('web:labor_timesheet')}?{urlencode(here)}")

    sheet = timekeeping.build_timesheet(request.user, chosen[0], chosen[1], first, last, today) if chosen else None
    nav = lambda step: urlencode({**here, "date": sitesheet.shift_anchor(period, first, step).isoformat()})
    perms = request.user.has_perm
    return render(request, "web/labor/timesheet.html", {
        "sheet": sheet, "sites": sites, "chosen": chosen, "period": period, "periods": sitesheet.PERIODS.items(),
        "anchor": anchor, "first": first, "last": last, "today": today,
        "prev_query": nav(-1), "next_query": nav(1),
        "next_is_future": sitesheet.shift_anchor(period, first, 1) > today,
        "fill_default": settings_service.get("labor.timesheet_fill_standard"),
        "can_save": perms(ACTION_PERMS["save"]), "can_confirm": perms(ACTION_PERMS["confirm"]),
        "can_reopen": perms(ACTION_PERMS["reopen"])})
