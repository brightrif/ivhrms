from datetime import date
from urllib.parse import urlencode

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone

from apps.configuration import services as settings_service
from apps.labor import sitesheet
from apps.labor.services import LaborError

from apps.web.access import hr_perm


def _anchor(value, today):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return today


@hr_perm("labor.view_laborallocation")
def labor_attendance(request):
    """HR's site attendance sheet. Pick a period (a day, a week or a month) and a site, tick off the exceptions,
    save. Anything already saved is shown and left alone."""
    today = timezone.localdate()
    data = request.POST if request.method == "POST" else request.GET
    period = data.get("period") if data.get("period") in sitesheet.PERIODS else settings_service.get("labor.sheet_period")
    anchor = min(_anchor(data.get("date"), today), today)
    first, last = sitesheet.period_bounds(period, anchor)
    sites = sitesheet.sites_in_period(request.user, first, last)
    chosen = next((s for s in sites if f"{s[0].pk}:{s[1].pk}" == data.get("site")), sites[0] if sites else None)
    here = {"period": period, "date": anchor.isoformat(), **({"site": f"{chosen[0].pk}:{chosen[1].pk}"} if chosen else {})}

    if request.method == "POST":
        if not request.user.has_perm("labor.add_laborallocation"):
            raise PermissionDenied
        if chosen is None:
            messages.error(request, "There is no site to save for in this period.")
        else:
            try:
                result = sitesheet.save_sheet(request.user, chosen[0], chosen[1], first, last, request.POST,
                                              fill=request.POST.get("fill", ""))
            except LaborError as exc:
                messages.error(request, str(exc))
            else:
                done = result["created"]
                if done:
                    parts = ", ".join(f"{n} {sitesheet.Status(s).label.lower()}" for s, n in done.items())
                    messages.success(request, f"Saved {sum(done.values())} entries: {parts}.")
                else:
                    messages.info(request, "Nothing to save. Fill in some days, or choose what to fill empty days with.")
                if result["skipped"]:
                    messages.warning(request, "Not saved: " + " | ".join(result["skipped"][:5]))
        return redirect(f"{reverse('web:labor_attendance')}?{urlencode(here)}")

    sheet = sitesheet.build_sheet(request.user, chosen[0], chosen[1], first, last, today) if chosen else None
    nav = lambda step: urlencode({**here, "date": sitesheet.shift_anchor(period, first, step).isoformat()})
    return render(request, "web/labor/attendance.html", {
        "sheet": sheet, "sites": sites, "chosen": chosen, "period": period, "periods": sitesheet.PERIODS.items(),
        "anchor": anchor, "first": first, "last": last, "today": today, "entry": sitesheet.ENTRY,
        "prev_query": nav(-1), "next_query": nav(1), "can_save": request.user.has_perm("labor.add_laborallocation"),
        "fill_default": settings_service.get("labor.attendance_fill_present"),
        "next_is_future": sitesheet.shift_anchor(period, first, 1) > today})
