import calendar
import csv

from django.http import HttpResponse
from django.shortcuts import render
from django.utils import timezone

from apps.organization.services import companies_for
from apps.vehicles import reports

from apps.web.access import hr_perm


def _number(raw, default, low, high):
    """A query-string number kept inside sensible limits, so a mistyped address never causes an error page."""
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return value if low <= value <= high else default


def _cell(value):
    """Stop a spreadsheet running a formula that was typed into a plate or make."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def _money(value):
    return "" if value is None else f"{value:.3f}"


def _csv(report):
    name = f"vehicle-costs-{report['year']}" + (f"-{report['month']:02d}" if report["month"] else "")
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{name}.csv"'
    response.write("\ufeff")                                       # so Excel reads the Arabic and accents correctly
    out = csv.writer(response)
    out.writerow(["Plate", "Vehicle", "Company", "Fuel", "Service and repairs", "Fines", "Renewal fees",
                  "Insurance recovered", "Running cost", "Loan payments", "Total", "Km driven",
                  "Running cost per km", "Fines charged to drivers"])
    for r in report["rows"]:
        out.writerow([_cell(r.vehicle.plate_number), _cell(r.vehicle.description), _cell(r.vehicle.company.name),
                      _money(r.fuel), _money(r.service), _money(r.fines), _money(r.renewals), _money(r.insurance),
                      _money(r.running), _money(r.loan), _money(r.total), "" if r.km is None else r.km,
                      _money(r.per_km), _money(r.recoverable)])
    t = report["totals"]
    out.writerow(["Total", "", "", _money(t.fuel), _money(t.service), _money(t.fines), _money(t.renewals),
                  _money(t.insurance), _money(t.running), _money(t.loan), _money(t.total), t.km or "",
                  _money(t.per_km), _money(t.recoverable)])
    return response


@hr_perm("vehicles.view_vehicle")
def vehicle_dashboard(request):
    return render(request, "web/vehicles/dashboard.html", reports.dashboard(request.user))


@hr_perm("vehicles.view_vehicleloan")
def vehicle_costs(request):
    """Money, including loan payments, so it is for the people who may see loans: Finance and Management."""
    today = timezone.localdate()
    year = _number(request.GET.get("year"), today.year, 2000, today.year)
    month = _number(request.GET.get("month"), 0, 0, 12)
    companies = companies_for(request.user)
    raw = request.GET.get("company", "")
    company_id = int(raw) if raw.isdigit() and companies.filter(pk=int(raw)).exists() else None
    report = reports.cost_report(request.user, year, month, company_id)
    if request.GET.get("export") == "csv":
        return _csv(report)
    return render(request, "web/vehicles/costs.html", {
        **report, "years": reports.available_years(request.user), "companies": companies,
        "multi_company": companies.count() > 1, "company": company_id or "",
        "month_choices": [(i, calendar.month_name[i]) for i in range(1, 13)],
        "period_label": f"{calendar.month_name[month]} {year}" if month else str(year)})
