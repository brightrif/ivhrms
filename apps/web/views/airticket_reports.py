"""Air ticket reports for HR, Finance and Management. Every one can be downloaded as CSV (opens in Excel)."""
from decimal import Decimal

from django.shortcuts import render
from django.utils import timezone

from apps.airtickets import reports
from apps.airtickets.models import TicketEntitlement
from apps.airtickets.ticketrequests import TicketRequest
from apps.organization.models import Department
from apps.organization.services import companies_for

from apps.web.access import hr_perm

PERM = "airtickets.view_ticketentitlement"
WINDOWS = [(30, "30 days"), (60, "60 days"), (90, "90 days"), (180, "180 days"), (365, "1 year")]


def _render(request, template, context):
    """`query` is the filters in the address, so the Download button can keep them."""
    return render(request, template, {"query": request.GET.urlencode(), **context})


def _int(raw, default=None):
    return int(raw) if str(raw or "").isdigit() else default


def _filters(request):
    """The company and department choices every report offers."""
    companies = companies_for(request.user)
    return {"companies": companies, "multi_company": companies.count() > 1,
            "departments": Department.objects.filter(company__in=companies, is_active=True).order_by("company", "name")}


def _year(request):
    available = reports.years(request.user)
    default = timezone.localdate().year if timezone.localdate().year in available or not available else available[0]
    raw = request.GET.get("year", "")
    year = None if raw == "all" else _int(raw, default)
    return year, available


@hr_perm(PERM)
def reports_index(request):
    user, today = request.user, timezone.localdate()
    rows = reports.utilised(user, year=today.year)
    _, grand = reports.expenses(rows, "nationality")
    return _render(request, "web/airtickets/reports.html", {
        "eligible_count": len(reports.eligible(user, within_days=90)),
        "pending_count": TicketRequest.objects.for_user(user).filter(status__in=TicketRequest.OPEN).count(),
        "used_count": grand["count"], "spent": grand["cost"], "year": today.year})


@hr_perm(PERM)
def report_eligible(request):
    within = _int(request.GET.get("within"), 90)
    company = _int(request.GET.get("company"))
    rows = reports.eligible(request.user, within_days=within, company=company)
    if request.GET.get("csv"):
        return reports.csv_response(
            "air-tickets-eligible.csv",
            ["Employee no", "Name", "Company", "Nationality", "Department", "Policy", "Due from", "Days", "Last ticket",
             "Request in progress"],
            [[e.employee.employee_no, e.employee.full_name, e.company.name, e.nationality_label, e.department_label,
              e.policy.name if e.policy_id else "", e.eligible_from.isoformat(), e.days,
              e.last_travel.isoformat() if e.last_travel else "", "Yes" if e.has_request else "No"] for e in rows])
    return _render(request, "web/airtickets/report_eligible.html", {
        "rows": rows, "within": within, "windows": WINDOWS, "company": company, **_filters(request)})


@hr_perm(PERM)
def report_used(request):
    year, available = _year(request)
    company, department = _int(request.GET.get("company")), _int(request.GET.get("department"))
    nationality = request.GET.get("nationality", "").strip()
    everything = reports.utilised(request.user, year=year, company=company, department=department)
    rows = [e for e in everything if not nationality or e.nationality_label == nationality]
    if request.GET.get("csv"):
        return reports.csv_response(
            f"air-tickets-used-{year or 'all'}.csv",
            ["Taken on", "Employee no", "Name", "Company", "Nationality", "Department", "How", "Flew to", "Cost (BHD)",
             "Notes"],
            [[e.used_on.isoformat(), e.employee.employee_no, e.employee.full_name, e.company.name, e.nationality_label,
              e.department_label, e.get_status_display(), e.destination, f"{e.cost:.3f}" if e.cost is not None else "",
              e.notes] for e in rows])
    total = sum((e.cost for e in rows if e.cost is not None), Decimal("0"))
    return _render(request, "web/airtickets/report_used.html", {
        "rows": rows, "total": total, "year": year, "years": available, "company": company, "department": department,
        "nationality": nationality, "nationalities": sorted({e.nationality_label for e in everything}),
        **_filters(request)})


@hr_perm(PERM)
def report_pending(request):
    company = _int(request.GET.get("company"))
    rows = reports.pending(request.user, company=company)
    if request.GET.get("csv"):
        return reports.csv_response(
            "air-tickets-pending.csv",
            ["Asked on", "Days waiting", "Employee no", "Name", "Company", "Departure", "Return", "Travellers", "How",
             "Status", "Waiting for", "Asked by"],
            [[r.created_at.date().isoformat(), r.age_days, r.employee.employee_no, r.employee.full_name, r.company.name,
              r.depart_on.isoformat(), r.return_on.isoformat() if r.return_on else "", len(r.travellers.all()),
              r.get_mode_display(), r.get_status_display(), r.waiting_for,
              r.requested_by.get_full_name() or r.requested_by.get_username()] for r in rows])
    return _render(request, "web/airtickets/report_pending.html", {"rows": rows, "company": company, **_filters(request)})


@hr_perm(PERM)
def report_expenses(request):
    year, available = _year(request)
    by = request.GET.get("by", "nationality")
    if by not in dict(reports.GROUPINGS):
        by = "nationality"
    company = _int(request.GET.get("company"))
    rows = reports.utilised(request.user, year=year, company=company)
    table, grand = reports.expenses(rows, by)
    label = dict(reports.GROUPINGS)[by]
    if request.GET.get("csv"):
        return reports.csv_response(
            f"air-tickets-cost-by-{by}-{year or 'all'}.csv",
            [label, "Tickets taken", "Allowances paid", "Total", "No cost recorded", "Total cost (BHD)",
             "Average (BHD)", "Share %"],
            [[r["label"], r["taken"], r["allowance"], r["count"], r["no_cost"], f"{r['cost']:.3f}",
              f"{r['average']:.3f}" if r["average"] is not None else "", r["share"]] for r in table]
            + [["All", grand["taken"], grand["allowance"], grand["count"], grand["no_cost"], f"{grand['cost']:.3f}",
                "", 100 if grand["cost"] else 0]])
    return _render(request, "web/airtickets/report_expenses.html", {
        "table": table, "grand": grand, "by": by, "by_label": label, "groupings": reports.GROUPINGS, "year": year,
        "years": available, "company": company, **_filters(request)})
