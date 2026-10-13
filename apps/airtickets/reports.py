"""Air ticket reports: who is eligible, the tickets used, the requests in progress, and what it all costs."""
import csv
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.contrib.contenttypes.models import ContentType
from django.db.models import Exists, Max, OuterRef
from django.http import HttpResponse
from django.utils import timezone

from apps.core.models import ApprovalRequest, ApprovalStep
from apps.employees.models import Employee

from .models import TicketEntitlement
from .nationalities import canonical
from .ticketrequests import TicketRequest

Status = TicketEntitlement.Status
ZERO = Decimal("0")
NO_NATIONALITY, NO_DEPARTMENT = "Not set", "No department"
GROUPINGS = [("nationality", "Nationality"), ("department", "Department"), ("company", "Company"),
             ("month", "Month")]


def nationality_label(text):
    return canonical(text) or (text or "").strip() or NO_NATIONALITY


def _decorate(entitlements):
    for e in entitlements:
        e.nationality_label = nationality_label(e.employee.nationality)
        e.department_label = e.employee.department.name if e.employee.department_id else NO_DEPARTMENT
    return entitlements


# ------------------------------------------------------------------ tickets used

def years(user):
    taken = (TicketEntitlement.objects.for_user(user).filter(status__in=(Status.USED, Status.PAID),
                                                             used_on__isnull=False).values_list("used_on", flat=True))
    return sorted({d.year for d in taken}, reverse=True)


def utilised(user, *, year=None, company=None, department=None, nationality=None):
    """Tickets taken and allowances paid (closed entitlements), newest first."""
    qs = (TicketEntitlement.objects.for_user(user)
          .filter(status__in=(Status.USED, Status.PAID), used_on__isnull=False)
          .select_related("employee__department", "company").order_by("-used_on", "employee__employee_no"))
    if year:
        qs = qs.filter(used_on__year=year)
    if company:
        qs = qs.filter(company_id=company)
    if department:
        qs = qs.filter(employee__department_id=department)
    rows = _decorate(list(qs))
    if nationality:
        rows = [e for e in rows if e.nationality_label == nationality]
    return rows


# ------------------------------------------------------------------ what it costs

def expenses(rows, by):
    """The cost of the rows given, grouped. A ticket recorded without a cost counts as taken but adds nothing, and is
    counted separately so a gap in the data is visible."""
    keys = {"nationality": lambda e: e.nationality_label, "department": lambda e: e.department_label,
            "company": lambda e: e.company.name, "month": lambda e: f"{e.used_on:%Y-%m}"}
    key = keys.get(by, keys["nationality"])
    groups = defaultdict(lambda: {"taken": 0, "allowance": 0, "cost": ZERO, "no_cost": 0})
    for e in rows:
        g = groups[key(e)]
        g["taken" if e.status == Status.USED else "allowance"] += 1
        if e.cost is None:
            g["no_cost"] += 1
        else:
            g["cost"] += e.cost
    table = [{"label": label, "count": g["taken"] + g["allowance"], **g} for label, g in groups.items()]
    table.sort(key=(lambda r: r["label"]) if by == "month" else (lambda r: (-r["cost"], r["label"])))
    total = sum((r["cost"] for r in table), ZERO)
    for r in table:
        costed = r["count"] - r["no_cost"]
        r["average"] = r["cost"] / costed if costed else None
        r["share"] = int(round(100 * r["cost"] / total)) if total else 0
    grand = {"count": sum(r["count"] for r in table), "taken": sum(r["taken"] for r in table),
             "allowance": sum(r["allowance"] for r in table), "no_cost": sum(r["no_cost"] for r in table),
             "cost": total}
    return table, grand


# ------------------------------------------------------------------ who is eligible

def eligible(user, *, within_days=90, company=None):
    """Staff whose next ticket is due now or within `within_days`, soonest first."""
    today = timezone.localdate()
    in_progress = TicketRequest.objects.filter(employee=OuterRef("employee"), status__in=TicketRequest.OPEN)
    qs = (TicketEntitlement.objects.for_user(user)
          .filter(status=Status.OPEN, employee__worker_type=Employee.WorkerType.STAFF,
                  eligible_from__lte=today + timedelta(days=within_days))
          .exclude(employee__status=Employee.Status.SEPARATED)
          .select_related("employee__department", "policy", "company")
          .annotate(has_request=Exists(in_progress)).order_by("eligible_from", "employee__employee_no"))
    if company:
        qs = qs.filter(company_id=company)
    rows = _decorate(list(qs))
    last = dict(TicketEntitlement.objects.filter(employee_id__in=[e.employee_id for e in rows],
                                                 status__in=(Status.USED, Status.PAID))
                .order_by().values_list("employee_id").annotate(last=Max("used_on")))
    for e in rows:
        e.days = (e.eligible_from - today).days
        e.last_travel = last.get(e.employee_id)
    return rows


# ------------------------------------------------------------------ requests in progress

def pending(user, *, company=None):
    """Requests still in progress, oldest first, and what each one is waiting for."""
    today = timezone.localdate()
    qs = (TicketRequest.objects.for_user(user).filter(status__in=TicketRequest.OPEN)
          .select_related("employee__department", "company", "requested_by").prefetch_related("travellers")
          .order_by("created_at"))
    if company:
        qs = qs.filter(company_id=company)
    rows = list(qs)
    ct = ContentType.objects.get_for_model(TicketRequest)
    waiting = {a.object_id: a for a in ApprovalRequest.objects.filter(
        content_type=ct, object_id__in=[str(r.pk) for r in rows], status=ApprovalRequest.Status.PENDING)}
    names = {(s.flow_id, s.order): s.name
             for s in ApprovalStep.objects.filter(flow_id__in={a.flow_id for a in waiting.values()})}
    for r in rows:
        r.age_days = (today - timezone.localtime(r.created_at).date()).days
        r.nationality_label = nationality_label(r.employee.nationality)
        r.department_label = r.employee.department.name if r.employee.department_id else NO_DEPARTMENT
        if r.status == TicketRequest.Status.PENDING:
            a = waiting.get(str(r.pk))
            r.waiting_for = "Approval: " + names.get((a.flow_id, a.current_step), "approver") if a else "Approval"
        elif r.status == TicketRequest.Status.APPROVED:
            r.waiting_for = "HR: record the allowance" if r.mode == "allowance" else "HR: issue the tickets"
        else:
            r.waiting_for = f"Travel on {r.depart_on:%d %b %Y}"
    return rows


# ------------------------------------------------------------------ download

def _safe(cell):
    """A spreadsheet runs a cell that starts with = + - @ as a formula. Typed text should never do that."""
    if isinstance(cell, str) and cell[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + cell
    return cell


def csv_response(filename, header, rows):
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response.write("\ufeff")                              # so Excel reads the accents and Arabic correctly
    writer = csv.writer(response)
    writer.writerow(header)
    for row in rows:
        writer.writerow([_safe(c) if c is not None else "" for c in row])
    return response
