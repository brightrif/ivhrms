from datetime import date, timedelta
from urllib.parse import urlencode

from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import render
from django.utils import timezone

from apps.labor import reporting, sitesheet
from apps.labor.models import Contractor, LaborProfile
from apps.organization.services import companies_for

from apps.web.access import hr_perm

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MAX_DAYS = 366
REPORTS = [
    ("labor_report_deployment", "Deployment", "labor.view_laborallocation"),
    ("labor_report_manpower", "Manpower by day", "labor.view_laborallocation"),
    ("labor_report_cost", "Cost", "labor.view_laborrate"),
    ("labor_report_contractor", "Contractor statement", "labor.view_laborrate"),
]


def _date(value, default):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return default


def _tables(report):
    out = []
    for table in report.tables:
        rows, total = reporting.display(table)
        out.append({"title": table.title, "columns": table.columns, "rows": rows, "total": total, "note": table.note})
    return out


def _respond(request, report, key, filename, controls):
    """The report on screen, or as an Excel download when the address ends with format=xlsx."""
    if request.GET.get("format") == "xlsx":
        response = HttpResponse(reporting.to_xlsx(report), content_type=XLSX)
        response["Content-Disposition"] = f'attachment; filename="{filename}.xlsx"'
        return response
    params = {k: v for k, v in request.GET.items() if k != "format"}
    pills = [(name, label) for name, label, perm in REPORTS if request.user.has_perm(perm)]
    return render(request, "web/labor/report.html", {
        "report": report, "tables": _tables(report), "key": key, "pills": pills,
        "export_query": urlencode({**params, "format": "xlsx"}), **controls})


def _range(request, today):
    """First and last day from the address, kept to a sensible size. Returns (first, last, error)."""
    first = _date(request.GET.get("from"), today.replace(day=1))
    last = _date(request.GET.get("to"), today)
    if last < first:
        return first, first, "The end date is before the start date."
    if (last - first).days >= MAX_DAYS:
        return first, first + timedelta(days=MAX_DAYS - 1), f"A report covers at most {MAX_DAYS} days; it was cut to fit."
    return first, last, ""


@hr_perm("labor.view_laborallocation")
def report_deployment(request):
    on = _date(request.GET.get("date"), timezone.localdate())
    report = reporting.deployment_report(request.user, on)
    return _respond(request, report, "labor_report_deployment", f"labor-deployment-{on:%Y-%m-%d}", {"on": on})


@hr_perm("labor.view_laborallocation")
def report_manpower(request):
    today = timezone.localdate()
    period = request.GET.get("period") if request.GET.get("period") in sitesheet.PERIODS else "month"
    anchor = min(_date(request.GET.get("date"), today), today)
    first, last = sitesheet.period_bounds(period, anchor)
    report = reporting.manpower_report(request.user, first, last)
    here = {"period": period}
    nav = lambda step: urlencode({**here, "date": sitesheet.shift_anchor(period, first, step).isoformat()})
    return _respond(request, report, "labor_report_manpower", f"labor-manpower-{first:%Y-%m-%d}_{last:%Y-%m-%d}", {
        "period": period, "periods": sitesheet.PERIODS.items(), "anchor": anchor, "first": first, "last": last,
        "today": today, "prev_query": nav(-1), "next_query": nav(1),
        "next_is_future": sitesheet.shift_anchor(period, first, 1) > today})


@hr_perm("labor.view_laborrate")
def report_cost(request):
    today = timezone.localdate()
    first, last, error = _range(request, today)
    if error:
        messages.warning(request, error)
    group = request.GET.get("group") if request.GET.get("group") in reporting.GROUPS else "site"
    companies = list(companies_for(request.user))
    company = request.GET.get("company", "")
    company = company if any(str(c.pk) == company for c in companies) else ""
    engagement = request.GET.get("engagement") if request.GET.get("engagement") in LaborProfile.Engagement.values else ""
    report = reporting.cost_report(request.user, first, last, group, company=company, engagement=engagement)
    return _respond(request, report, "labor_report_cost", f"labor-cost-{first:%Y-%m-%d}_{last:%Y-%m-%d}-by-{group}", {
        "first": first, "last": last, "today": today, "group": group, "groups": reporting.GROUPS.items(),
        "companies": companies, "multi_company": len(companies) > 1, "company": company, "engagement": engagement,
        "engagements": LaborProfile.Engagement.choices})


@hr_perm("labor.view_laborrate")
def report_contractor(request):
    today = timezone.localdate()
    first, last, error = _range(request, today)
    if error:
        messages.warning(request, error)
    contractors = list(Contractor.objects.for_user(request.user).select_related("company").order_by("name"))
    wanted = request.GET.get("contractor", "")
    valid = {"none"} | {str(c.pk) for c in contractors}
    chosen = wanted if wanted in valid else (str(contractors[0].pk) if contractors else "none")
    report = reporting.contractor_statement(request.user, first, last, chosen)
    return _respond(request, report, "labor_report_contractor",
                    f"labor-contractor-{chosen}-{first:%Y-%m-%d}_{last:%Y-%m-%d}", {
                        "first": first, "last": last, "today": today, "contractors": contractors, "contractor": chosen})
