"""Labor reports. Each builder returns a Report (tables of plain values), which the pages show and the Excel export
writes, so the screen and the file can never disagree.

Cost is an estimate for management, made before payroll exists. The rules it uses are listed on every cost report."""
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.db.models import Q
from django.utils import timezone

from apps.attendance.models import Attendance
from apps.employees.models import Employee
from apps.organization.services import companies_for
from apps.scheduling.services import HOLIDAY, WEEKLY_OFF, day_types

from . import xlsx
from .allocation import LaborAllocation
from .deployment import unallocated_profiles
from .models import LaborProfile, LaborRate
from .overtime import OvertimeClaim, OvertimePolicy
from .timesheet import TimeEntry

ZERO = Decimal("0")
THREE, TWO = Decimal("0.001"), Decimal("0.01")
NO_SITE = "(no site recorded)"
NO_ORDER = "(no work order)"
DIRECT = "Direct employees"
CONTRACTED_DIRECT = "Contracted, engaged directly"
SPECIAL_LABELS = (NO_SITE, NO_ORDER, DIRECT, CONTRACTED_DIRECT)       # listed after the real names
GROUPS = {"site": "Site", "work_order": "Work order", "trade": "Trade", "contractor": "Contractor", "worker": "Worker"}


# ------------------------------------------------------------------ the shape every report has

@dataclass
class Column:
    label: str
    kind: str = "text"            # text | int | hours | money | date


@dataclass
class Table:
    title: str
    columns: list
    rows: list
    total: list = None            # a row like the others, shown in bold at the bottom
    note: str = ""


@dataclass
class Report:
    title: str
    subtitle: str
    tables: list
    notes: list = field(default_factory=list)


def show(kind, value):
    """(text, is_number) for a value in a column of this kind."""
    if value is None or value == "":
        return "", kind != "text"
    if kind == "int":
        return f"{int(value):,}", True
    if kind == "hours":
        return f"{Decimal(value).quantize(TWO, ROUND_HALF_UP):,}", True
    if kind == "money":
        return f"{Decimal(value).quantize(THREE, ROUND_HALF_UP):,}", True
    if kind == "date":
        return f"{value:%d %b %Y}", False
    return str(value), False


def display(table):
    """The table as (text, is_number) pairs, ready for a template."""
    rows = [[show(c.kind, v) for c, v in zip(table.columns, row)] for row in table.rows]
    total = [show(c.kind, v) for c, v in zip(table.columns, table.total)] if table.total else None
    return rows, total


_STYLE = {"int": (xlsx.INT, xlsx.INT_BOLD), "hours": (xlsx.HOURS, xlsx.HOURS_BOLD),
          "money": (xlsx.MONEY, xlsx.MONEY_BOLD), "date": (xlsx.DATE, xlsx.DATE), "text": (xlsx.DEFAULT, xlsx.TEXT_BOLD)}


def to_xlsx(report):
    """The report as an Excel file: one sheet per table, with the title, the period and the notes on each."""
    sheets = []
    for table in report.tables:
        rows = [[xlsx.Cell(report.title, xlsx.TITLE)], [xlsx.Cell(report.subtitle, xlsx.NOTE)],
                [xlsx.Cell(table.title, xlsx.TEXT_BOLD)] if len(report.tables) > 1 else [],
                [xlsx.Cell(c.label, xlsx.HEADER) for c in table.columns]]
        for row in table.rows:
            rows.append([xlsx.Cell(_native(c.kind, v), _STYLE[c.kind][0]) for c, v in zip(table.columns, row)])
        if table.total:
            rows.append([xlsx.Cell(_native(c.kind, v), _STYLE[c.kind][1]) for c, v in zip(table.columns, table.total)])
        rows.append([])
        for line in ([table.note] if table.note else []) + report.notes:
            rows.append([xlsx.Cell(line, xlsx.NOTE)])
        widths = []
        for i, c in enumerate(table.columns):
            longest = max([len(c.label)] + [len(show(c.kind, r[i])[0]) for r in table.rows] + ([len(show(c.kind, table.total[i])[0])] if table.total else [0]))
            widths.append(min(max(longest + 2, 10), 42))
        sheets.append(xlsx.Sheet(table.title, rows, widths=widths, freeze_row=4))
    return xlsx.build_workbook(sheets)


def _native(kind, value):
    if kind == "money" and value is not None:
        return Decimal(value).quantize(THREE, ROUND_HALF_UP)
    if kind == "hours" and value is not None:
        return Decimal(value).quantize(TWO, ROUND_HALF_UP)
    return value


# ------------------------------------------------------------------ deployment: who is where, on a date

def _contractor_label(profile):
    if profile.contractor_id:
        return profile.contractor.name
    return DIRECT if profile.engagement == LaborProfile.Engagement.DIRECT else CONTRACTED_DIRECT


def _pivot(title, label, rows, sites):
    """Rows (a label per worker) against sites, as counts. `rows` is a list of (site key, row label)."""
    counts = defaultdict(lambda: defaultdict(int))
    for site, name in rows:
        counts[name][site] += 1
    names = sorted(counts, key=lambda n: (n in (DIRECT, CONTRACTED_DIRECT), n.lower()))
    columns = [Column(label)] + [Column(site_label, "int") for _, site_label in sites] + [Column("Total", "int")]
    body = [[name] + [counts[name].get(k, 0) for k, _ in sites] + [sum(counts[name].values())] for name in names]
    total = ["Total"] + [sum(r[i] for r in body) for i in range(1, len(columns))] if body else None
    return Table(title, columns, body, total)


def deployment_report(user, on_date):
    allocations = list(
        LaborAllocation.objects.for_user(user).filter(effective_from__lte=on_date)
        .filter(Q(effective_to__isnull=True) | Q(effective_to__gte=on_date))
        .exclude(employee__status=Employee.Status.SEPARATED)
        .select_related("project", "location", "employee__labor_profile__trade", "employee__labor_profile__contractor"))
    sites = {}
    for a in allocations:
        sites[(a.project_id, a.location_id)] = (a.project.code, a.project.name, a.location.name)
    ordered = sorted(sites.items(), key=lambda kv: (kv[1][0], kv[1][2]))
    site_cols = [(k, f"{code} / {site}") for k, (code, name, site) in ordered]
    by_site, trade_rows, contractor_rows = {}, [], []
    for a in allocations:
        profile = a.employee.labor_profile
        row = by_site.setdefault((a.project_id, a.location_id), [0, 0])
        row[0 if profile.engagement == LaborProfile.Engagement.DIRECT else 1] += 1
        key = (a.project_id, a.location_id)
        trade_rows.append((key, profile.trade.name))
        contractor_rows.append((key, _contractor_label(profile)))
    body = [[f"{code} {name}", site, by_site[k][0], by_site[k][1], sum(by_site[k])] for k, (code, name, site) in ordered]
    total = ["Total", "", sum(r[2] for r in body), sum(r[3] for r in body), sum(r[4] for r in body)] if body else None
    tables = [Table("By site", [Column("Project"), Column("Site"), Column("Direct", "int"), Column("Contracted", "int"),
                                Column("Total", "int")], body, total),
              _pivot("By trade", "Trade", trade_rows, site_cols),
              _pivot("By contractor", "Contractor", contractor_rows, site_cols)]
    notes = ["Counts the workers allocated to each site on this date. Workers who have since left the company are not counted."]
    if on_date >= timezone.localdate():
        n = unallocated_profiles(user).count()
        if n:
            notes.append(f"{n} worker{'s are' if n != 1 else ' is'} not on any site.")
    return Report("Labor deployment", f"As of {on_date:%d %b %Y}", tables, notes)


# ------------------------------------------------------------------ manpower: who actually worked, day by day

def manpower_report(user, first, last):
    days = [first + timedelta(days=i) for i in range((last - first).days + 1)]
    records = (Attendance.objects.filter(employee__labor_profile__isnull=False,
                                         employee__company__in=companies_for(user), date__range=(first, last),
                                         status__in=Attendance.WORKED).select_related("project", "location"))
    people, mandays = defaultdict(lambda: defaultdict(int)), defaultdict(Decimal)
    for r in records:
        site = f"{r.project.code} / {r.location.name}" if r.project_id and r.location_id else NO_SITE
        people[site][r.date] += 1
        mandays[site] += Decimal(str(Attendance.DAY_FRACTION[r.status]))
    names = sorted(people, key=lambda s: (s == NO_SITE, s))
    columns = [Column("Site")] + [Column(f"{d.day} {d:%a}"[:6], "int") for d in days] + [Column("Man-days", "hours")]
    rows = [[n] + [people[n].get(d, 0) for d in days] + [mandays[n]] for n in names]
    total = ["Total"] + [sum(r[i] for r in rows) for i in range(1, len(days) + 1)] + [sum((r[-1] for r in rows), ZERO)] if rows else None
    return Report("Manpower by day", f"{first:%d %b %Y} to {last:%d %b %Y}",
                  [Table("Manpower", columns, rows, total)],
                  ["People who worked each day, from attendance. A half day counts as one person and half a man-day."])


# ------------------------------------------------------------------ cost: what the labor on each site costs

@dataclass
class Fact:
    employee: object
    project: object
    location: object
    work_order: object
    days: Decimal = ZERO
    hours: Decimal = ZERO
    wages: Decimal = ZERO
    ot_hours: Decimal = ZERO
    ot_approved: Decimal = ZERO
    ot_pending: Decimal = ZERO


def collect_facts(user, first, last, *, company="", engagement="", contractor="", today=None):
    """Everything the cost reports add up, one Fact per worker, project, site and work order."""
    today = today or timezone.localdate()
    end = min(last, today)
    profiles = LaborProfile.objects.for_user(user).select_related("employee", "employee__company", "trade", "contractor")
    if str(company).isdigit():
        profiles = profiles.filter(company_id=company)
    if engagement in LaborProfile.Engagement.values:
        profiles = profiles.filter(engagement=engagement)
    if contractor == "none":
        profiles = profiles.filter(contractor__isnull=True)
    elif str(contractor).isdigit():
        profiles = profiles.filter(contractor_id=contractor)
    profiles = list(profiles)
    by_emp = {p.employee_id: p for p in profiles}
    ids = list(by_emp)
    facts = {}
    if not ids or end < first:
        return facts, by_emp

    rates = defaultdict(list)
    for r in LaborRate.objects.filter(profile__employee_id__in=ids).select_related("profile"):
        rates[r.profile.employee_id].append(r)
    spans = defaultdict(list)
    for a in (LaborAllocation.objects.filter(employee_id__in=ids, effective_from__lte=end)
              .filter(Q(effective_to__isnull=True) | Q(effective_to__gte=first)).select_related("project", "location", "work_order")):
        spans[a.employee_id].append(a)
    attendance = {}
    for rec in Attendance.objects.filter(employee_id__in=ids, date__range=(first, end)).select_related("project", "location"):
        attendance[(rec.employee_id, rec.date)] = rec
    policies = defaultdict(list)
    for p in OvertimePolicy.objects.filter(company_id__in={p.company_id for p in profiles}):
        policies[p.company_id].append(p)

    def rate_on(emp_id, d):
        return next((r for r in rates[emp_id] if r.effective_from <= d and (r.effective_to is None or d <= r.effective_to)), None)

    def span_on(emp_id, d):
        return next((a for a in spans[emp_id] if a.effective_from <= d and (a.effective_to is None or d <= a.effective_to)), None)

    def policy_on(company_id, d):
        return next((p for p in policies[company_id] if p.effective_from <= d and (p.effective_to is None or d <= p.effective_to)), None)

    def fact(emp, project, location, work_order):
        key = (emp.pk, getattr(project, "pk", None), getattr(location, "pk", None), getattr(work_order, "pk", None))
        if key not in facts:
            facts[key] = Fact(emp, project, location, work_order)
        return facts[key]

    kinds = {}
    # days worked, and the wages of daily-rate workers, come from attendance
    for (emp_id, d), rec in attendance.items():
        if rec.status not in Attendance.WORKED:
            continue
        emp = by_emp[emp_id].employee
        span = span_on(emp_id, d)
        project = rec.project or (span.project if span else None)
        location = rec.location or (span.location if span else None)
        order = span.work_order if span and project and span.project_id == project.pk and span.location_id == getattr(location, "pk", None) else None
        f = fact(emp, project, location, order)
        fraction = Decimal(str(Attendance.DAY_FRACTION[rec.status]))
        f.days += fraction
        rate = rate_on(emp_id, d)
        if rate and rate.wage_basis == LaborRate.WageBasis.DAILY:
            if emp_id not in kinds:
                kinds[emp_id] = day_types(emp, first, end)
            policy = policy_on(emp.company_id, d)
            paid_as_overtime = (kinds[emp_id][d] in (HOLIDAY, WEEKLY_OFF) and policy is not None
                                and policy.overtime_applies and policy.all_hours_on_days_off)
            if not paid_as_overtime:
                f.wages += rate.rate * fraction

    # a monthly salary is spread over every calendar day the worker was allocated, less absences
    for emp_id, allocation_list in spans.items():
        emp = by_emp[emp_id].employee
        for a in allocation_list:
            d, stop = max(a.effective_from, first, emp.joining_date), min(a.effective_to or end, end)
            while d <= stop:
                rate = rate_on(emp_id, d)
                rec = attendance.get((emp_id, d))
                if rate and rate.wage_basis == LaborRate.WageBasis.MONTHLY and not (
                        rec and rec.status in (Attendance.Status.ABSENT, Attendance.Status.UNPAID_LEAVE)):
                    policy = policy_on(emp.company_id, d)
                    divisor = policy.monthly_divisor if policy else 30
                    fact(emp, a.project, a.location, a.work_order).wages += rate.rate / divisor
                d += timedelta(days=1)

    for entry in (TimeEntry.objects.filter(employee_id__in=ids, date__range=(first, end))
                  .select_related("project", "location", "work_order")):
        fact(by_emp[entry.employee_id].employee, entry.project, entry.location, entry.work_order).hours += entry.hours

    for claim in (OvertimeClaim.objects.filter(employee_id__in=ids, date__range=(first, end))
                  .select_related("project", "location", "work_order")):
        f = fact(by_emp[claim.employee_id].employee, claim.project, claim.location, claim.work_order)
        if claim.status == OvertimeClaim.Status.APPROVED:
            f.ot_hours += claim.hours
            f.ot_approved += claim.amount
        elif claim.status == OvertimeClaim.Status.PENDING:
            f.ot_pending += claim.amount
    return facts, by_emp


def _site_label(f):
    return f"{f.project.code} / {f.location.name}" if f.project and f.location else NO_SITE


def _group_parts(group, f, profile):
    if group == "site":
        return [f.project.code if f.project else "", f.project.name if f.project else "", f.location.name if f.location else NO_SITE]
    if group == "work_order":
        return [f.project.code if f.project else NO_SITE, f.work_order.code if f.work_order else NO_ORDER,
                f.work_order.name if f.work_order else ""]
    if group == "trade":
        return [profile.trade.name]
    if group == "contractor":
        return [_contractor_label(profile)]
    return [f.employee.employee_no, f.employee.full_name, profile.trade.name]


_LABELS = {"site": ["Project", "Project name", "Site"], "work_order": ["Project", "Work order", "Description"],
           "trade": ["Trade"], "contractor": ["Contractor"], "worker": ["No.", "Worker", "Trade"]}

BASIS = [
    "An estimate for cost control, made before payroll. Total = wages + approved overtime.",
    "Daily-wage workers: days worked x the daily rate in force that day (a half day is half). Absence and leave are not paid.",
    "Monthly-salary workers: salary / days in a month (from the overtime rules, 30 if there are none), for every calendar day "
    "allocated to the site except days marked absent or unpaid leave.",
    "A daily-wage worker's day on a weekly off or holiday counts as a day worked but is paid through overtime when the company's "
    "rules count every hour on a day off as overtime.",
    "Hours come from timesheets (draft and confirmed). Overtime pending approval is shown separately and is not in the total; "
    "rejected overtime is ignored.",
]


def _money(v):
    return v.quantize(THREE, ROUND_HALF_UP)


def cost_report(user, first, last, group_by="site", *, company="", engagement="", contractor=""):
    group_by = group_by if group_by in GROUPS else "site"
    facts, profiles = collect_facts(user, first, last, company=company, engagement=engagement, contractor=contractor)
    grouped = {}
    for f in facts.values():
        key = tuple(_group_parts(group_by, f, profiles[f.employee.pk]))
        g = grouped.setdefault(key, {"workers": set(), "days": ZERO, "hours": ZERO, "wages": ZERO, "ot_hours": ZERO,
                                     "ot_approved": ZERO, "ot_pending": ZERO})
        g["workers"].add(f.employee.pk)
        for name in ("days", "hours", "wages", "ot_hours", "ot_approved", "ot_pending"):
            g[name] += getattr(f, name)
    keys = sorted(grouped, key=lambda k: (any(x in SPECIAL_LABELS for x in k), [x.lower() for x in k]))
    labels = _LABELS[group_by]
    columns = [Column(x) for x in labels] + [Column("Workers", "int"), Column("Days worked", "hours"), Column("Hours", "hours"),
                                             Column("Wages (BHD)", "money"), Column("Overtime hours", "hours"),
                                             Column("Overtime approved (BHD)", "money"), Column("Total (BHD)", "money"),
                                             Column("Overtime pending (BHD)", "money")]
    rows = []
    for k in keys:
        g = grouped[k]
        rows.append(list(k) + [len(g["workers"]), g["days"], g["hours"], _money(g["wages"]), g["ot_hours"],
                               _money(g["ot_approved"]), _money(g["wages"] + g["ot_approved"]), _money(g["ot_pending"])])
    n = len(labels)
    total = None
    if rows:
        everyone = {f.employee.pk for f in facts.values()}
        total = ["Total"] + [""] * (n - 1) + [len(everyone)] + [sum((r[i] for r in rows), ZERO) for i in range(n + 1, n + 8)]
    subtitle = f"{first:%d %b %Y} to {last:%d %b %Y}, by {GROUPS[group_by].lower()}"
    return Report("Labor cost", subtitle, [Table(f"By {GROUPS[group_by].lower()}", columns, rows, total)], list(BASIS))


# ------------------------------------------------------------------ what a contractor should be paid

def contractor_statement(user, first, last, contractor, *, company=""):
    """Amount payable for the workers a contractor supplied (or, with contractor="none", for contracted workers the
    client engages directly): days worked x daily rate, plus approved overtime."""
    facts, profiles = collect_facts(user, first, last, company=company, engagement=LaborProfile.Engagement.CONTRACTED,
                                    contractor=contractor)
    per_worker, per_site = {}, {}
    for f in facts.values():
        w = per_worker.setdefault(f.employee.pk, {"f": f, "days": ZERO, "wages": ZERO, "ot": ZERO, "pending": ZERO})
        w["days"] += f.days
        w["wages"] += f.wages
        w["ot"] += f.ot_approved
        w["pending"] += f.ot_pending
        s = per_site.setdefault((f.project.code if f.project else NO_SITE, f.location.name if f.location else "",
                                 f.work_order.code if f.work_order else NO_ORDER), {"days": ZERO, "wages": ZERO, "ot": ZERO})
        s["days"] += f.days
        s["wages"] += f.wages
        s["ot"] += f.ot_approved
    first_rates = defaultdict(set)
    for r in LaborRate.objects.filter(profile__employee_id__in=list(per_worker)).select_related("profile"):
        if r.effective_from <= last and (r.effective_to is None or r.effective_to >= first):
            first_rates[r.profile.employee_id].add(r.rate.quantize(THREE))
    rows = []
    for emp_id, w in sorted(per_worker.items(), key=lambda kv: kv[1]["f"].employee.employee_no):
        profile = profiles[emp_id]
        rates = sorted(first_rates[emp_id])
        rows.append([w["f"].employee.employee_no, w["f"].employee.full_name, profile.trade.name, w["days"],
                     f"{rates[0]}" if len(rates) == 1 else ("varies" if rates else ""), _money(w["wages"]), _money(w["ot"]),
                     _money(w["wages"] + w["ot"])])
    columns = [Column("No."), Column("Worker"), Column("Trade"), Column("Days worked", "hours"), Column("Daily rate (BHD)"),
               Column("Wages (BHD)", "money"), Column("Overtime approved (BHD)", "money"), Column("Amount payable (BHD)", "money")]
    total = ["Total", "", "", sum((r[3] for r in rows), ZERO), "", sum((r[5] for r in rows), ZERO),
             sum((r[6] for r in rows), ZERO), sum((r[7] for r in rows), ZERO)] if rows else None
    site_rows = [list(k) + [v["days"], _money(v["wages"]), _money(v["ot"]), _money(v["wages"] + v["ot"])]
                 for k, v in sorted(per_site.items())]
    site_total = ["Total", "", "", sum((r[3] for r in site_rows), ZERO), sum((r[4] for r in site_rows), ZERO),
                  sum((r[5] for r in site_rows), ZERO), sum((r[6] for r in site_rows), ZERO)] if site_rows else None
    who = "contracted workers engaged directly" if contractor == "none" else None
    if who is None:
        from .models import Contractor
        c = Contractor.objects.for_user(user).filter(pk=contractor).first() if str(contractor).isdigit() else None
        who = c.name if c else "all contractors"
    pending = sum((w["pending"] for w in per_worker.values()), ZERO)
    notes = ["Days worked x the daily rate, plus overtime that has been approved. Check it against the contractor's invoice."]
    if pending:
        notes.append(f"Overtime of {_money(pending)} BHD is still pending approval and is not included.")
    return Report("Contractor statement", f"{who}, {first:%d %b %Y} to {last:%d %b %Y}",
                  [Table("By worker", columns, rows, total),
                   Table("By site and work order", [Column("Project"), Column("Site"), Column("Work order"), Column("Days worked", "hours"),
                                                    Column("Wages (BHD)", "money"), Column("Overtime approved (BHD)", "money"),
                                                    Column("Amount payable (BHD)", "money")], site_rows, site_total)], notes)
