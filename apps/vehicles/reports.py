"""Cost report and fleet dashboard. Everything is read-only and scoped to the vehicles the user may see."""
import calendar
from collections import Counter
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from django.utils import timezone

from apps.compliance import schedule
from apps.compliance import services as compliance_services
from apps.compliance.models import RenewalPayment
from apps.compliance.services import add_months

from . import financing, handovers, maintenance
from .incidents import Accident, Fine
from .loans import LoanInstallment, VehicleLoan
from .models import Vehicle
from .upkeep import FuelFill, ServiceRecord
from .usage import OdometerReading, VehicleAssignment

ZERO = Decimal("0")
# What a vehicle costs the company, by kind. Insurance money recovered is a negative amount. Accident repairs are
# ordinary service records, so they are counted once, under "service". Loan payments are shown beside the running
# cost rather than inside it: repaying a loan is buying the vehicle, not running it.
RUNNING = ("fuel", "service", "fines", "renewals", "insurance")
KINDS = RUNNING + ("loan",)
INFO = "recoverable"                              # fines charged to the driver: shown, but not subtracted


def period(year, month=0):
    """First and last day of a month, or of the whole year when month is 0."""
    if month:
        return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])
    return date(year, 1, 1), date(year, 12, 31)


def available_years(user, today=None):
    today = today or timezone.localdate()
    first = Vehicle.objects.for_user(user).order_by("created_at").values_list("created_at", flat=True).first()
    return list(range(today.year, (first.year if first else today.year) - 1, -1))


def cost_events(user, start, end, vehicle_id=None):
    """Every cost in the date range as (vehicle id, day, kind, amount). Amounts are summed in Python, not SQL,
    because BHD has three decimals."""
    def only(qs, field="vehicle_id"):
        return qs.filter(**{field: vehicle_id}) if vehicle_id else qs

    events = []
    fuel = FuelFill.objects.for_user(user).filter(is_voided=False, filled_on__range=(start, end))
    for vid, day, cost in only(fuel).values_list("vehicle_id", "filled_on", "cost"):
        events.append((vid, day, "fuel", cost))

    service = ServiceRecord.objects.for_user(user).filter(is_voided=False, serviced_on__range=(start, end))
    for vid, day, parts, labour in only(service).values_list("vehicle_id", "serviced_on", "parts_cost", "labour_cost"):
        events.append((vid, day, "service", parts + labour))

    fines = Fine.objects.for_user(user).filter(is_voided=False, fined_on__range=(start, end))
    for vid, day, amount, paid, status, charged, recovered in only(fines).values_list(
            "vehicle_id", "fined_on", "amount", "paid_amount", "status", "charged_to_employee", "recovered_on"):
        cost = paid if (status == Fine.Status.PAID and paid is not None) else amount
        events.append((vid, day, "fines", cost))
        if charged and recovered is None:                       # still to be recovered from the driver
            events.append((vid, day, INFO, cost))

    accidents = Accident.objects.for_user(user).filter(is_voided=False, occurred_on__range=(start, end))
    for vid, day, recovered in only(accidents).values_list("vehicle_id", "occurred_on", "insurance_recovered"):
        if recovered:
            events.append((vid, day, "insurance", -recovered))

    renewals = RenewalPayment.objects.for_user(user).filter(task__document__vehicle__isnull=False,
                                                            paid_on__range=(start, end))
    for vid, day, gov, svc, fine in only(renewals, "task__document__vehicle_id").values_list(
            "task__document__vehicle_id", "paid_on", "government_fee", "service_fee", "fine"):
        events.append((vid, day, "renewals", gov + svc + fine))

    installments = LoanInstallment.objects.for_user(user).filter(
        status=LoanInstallment.Status.PAID, loan__is_voided=False, paid_on__range=(start, end))
    for vid, day, paid in only(installments, "loan__vehicle_id").values_list("loan__vehicle_id", "paid_on", "paid_amount"):
        events.append((vid, day, "loan", paid or ZERO))

    settled = VehicleLoan.objects.for_user(user).filter(status=VehicleLoan.Status.SETTLED, is_voided=False,
                                                        settled_on__range=(start, end))
    for vid, day, amount in only(settled).values_list("vehicle_id", "settled_on", "settlement_amount"):
        events.append((vid, day, "loan", amount or ZERO))
    return events


def _km_by_vehicle(user, start, end, vehicle_ids):
    """Kilometres driven in the period: the last reading in it minus the last one before it (or, for a vehicle
    first measured inside the period, the first reading in it). None when there is nothing to measure from."""
    rows = {}
    readings = (OdometerReading.objects.for_user(user).filter(is_voided=False, reading_on__lte=end,
                                                              vehicle_id__in=list(vehicle_ids))
                .order_by("vehicle_id", "reading_on", "id").values_list("vehicle_id", "reading_on", "odometer"))
    for vid, day, km in readings:
        rows.setdefault(vid, []).append((day, km))
    result = {}
    for vid, items in rows.items():
        before = [km for day, km in items if day < start]
        inside = [km for day, km in items if day >= start]
        if inside:
            result[vid] = inside[-1] - (before[-1] if before else inside[0])
    return result


def _month_buckets(first, months):
    return {add_months(first, i): {"running": ZERO, "loan": ZERO} for i in range(months)}


def _finish_buckets(buckets):
    rows = [SimpleNamespace(month=month, running=b["running"], loan=b["loan"], total=b["running"] + b["loan"])
            for month, b in buckets.items()]
    top = max((max(r.total, ZERO) for r in rows), default=ZERO)
    for r in rows:
        r.pct = int(100 * max(r.total, ZERO) / top) if top else 0
    return rows


def cost_report(user, year, month=0, company_id=None, vehicle_id=None):
    start, end = period(year, month)
    vehicles = Vehicle.objects.for_user(user).select_related("company")
    if company_id:
        vehicles = vehicles.filter(company_id=company_id)
    if vehicle_id:
        vehicles = vehicles.filter(pk=vehicle_id)
    allowed = {v.pk: v for v in vehicles}
    per = {pk: {k: ZERO for k in KINDS + (INFO,)} for pk in allowed}
    buckets = _month_buckets(date(year, 1, 1), 12)
    for vid, day, kind, amount in cost_events(user, start, end, vehicle_id):
        if vid not in allowed:
            continue
        per[vid][kind] += amount
        if kind in KINDS:
            buckets[day.replace(day=1)]["loan" if kind == "loan" else "running"] += amount
    km = _km_by_vehicle(user, start, end, allowed)

    rows = []
    for pk, vehicle in allowed.items():
        c = per[pk]
        running = sum((c[k] for k in RUNNING), ZERO)
        if not any(c[k] for k in KINDS) and not km.get(pk):
            continue
        rows.append(SimpleNamespace(
            vehicle=vehicle, **c, running=running, total=running + c["loan"], km=km.get(pk),
            per_km=running / km[pk] if km.get(pk) else None))
    rows.sort(key=lambda r: (-r.total, r.vehicle.plate_number))

    totals = SimpleNamespace(**{k: sum((r.__dict__[k] for r in rows), ZERO) for k in KINDS + (INFO,)})
    totals.running = sum((r.running for r in rows), ZERO)
    totals.total = totals.running + totals.loan
    measured = [r for r in rows if r.km]
    totals.km = sum((r.km for r in measured), 0)
    totals.per_km = sum((r.running for r in measured), ZERO) / totals.km if totals.km else None
    return {"rows": rows, "totals": totals, "start": start, "end": end, "year": year, "month": month,
            "months": _finish_buckets(buckets) if not month else None}


# ---------------------------------------------------------------- the fleet dashboard

def dashboard(user, today=None):
    """What needs attention across the fleet. Each section appears only if the user may see that kind of data,
    and money and loans are for Finance and Management."""
    today = today or timezone.localdate()
    can = {"services": user.has_perm("vehicles.view_serviceplan"), "fines": user.has_perm("vehicles.view_fine"),
           "accidents": user.has_perm("vehicles.view_accident"), "documents": user.has_perm("compliance.view_document"),
           "finance": user.has_perm("vehicles.view_vehicleloan")}
    counts = Counter(Vehicle.objects.for_user(user).values_list("status", flat=True))
    data = {"can": can, "today": today, "vehicles_total": sum(counts.values()),
            "counts": {s: counts[s] for s in (Vehicle.Status.ACTIVE, Vehicle.Status.WORKSHOP, Vehicle.Status.SOLD)}}

    leavers = list(
        VehicleAssignment.objects.for_user(user).filter(assigned_to__isnull=True,
                                                        employee__status__in=("on_notice", "separated"))
        .exclude(vehicle__status=Vehicle.Status.SOLD).select_related("vehicle", "employee"))
    for a in leavers:
        a.state = "left" if a.employee.status == "separated" else "notice"
        a.decision = handovers.current_decision(a)               # a request waiting for approval, or approved, or rejected
    data["leavers"] = leavers

    if can["services"]:
        plans = maintenance.due_plans(user, today)
        data.update(services=plans[:8], services_total=len(plans),
                    services_overdue=sum(1 for p in plans if p.status.state == maintenance.OVERDUE))
    if can["documents"]:
        docs = sorted((d for d in compliance_services.current_documents(user)
                       if d.vehicle_id and d.state in (schedule.EXPIRED, schedule.DUE)), key=lambda d: d.expiry_date)
        data.update(documents=docs[:8], documents_total=len(docs),
                    documents_expired=sum(1 for d in docs if d.state == schedule.EXPIRED))
    if can["fines"]:
        fines = list(Fine.objects.for_user(user).filter(is_voided=False, status=Fine.Status.UNPAID)
                     .select_related("vehicle", "driver").order_by("fined_on"))
        data.update(fines=fines[:8], fines_total=len(fines), fines_amount=sum((f.amount for f in fines), ZERO))
    if can["accidents"]:
        open_accidents = list(Accident.objects.for_user(user).filter(is_voided=False, status=Accident.Status.OPEN)
                              .select_related("vehicle", "driver"))
        data.update(accidents=open_accidents[:8], accidents_total=len(open_accidents))
    if can["finance"]:
        loans = financing.overview(user, today)
        late = [l for l in loans["loans"] if l.summary.overdue_count]
        first = add_months(today.replace(day=1), -11)
        buckets = _month_buckets(first, 12)
        ytd_running = ytd_loan = ZERO
        for vid, day, kind, amount in cost_events(user, first, today):
            if kind not in KINDS:
                continue
            buckets[day.replace(day=1)]["loan" if kind == "loan" else "running"] += amount
            if day.year == today.year:
                if kind == "loan":
                    ytd_loan += amount
                else:
                    ytd_running += amount
        trend = _finish_buckets(buckets)
        data.update(loans=loans, loans_late=late, trend=trend, cost_month=trend[-1].running,
                    cost_ytd=ytd_running, loan_ytd=ytd_loan)
    return data
