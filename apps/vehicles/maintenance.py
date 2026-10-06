import logging
from types import SimpleNamespace

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import send_mass_mail
from django.db import transaction
from django.db.models import Prefetch
from django.urls import NoReverseMatch, reverse
from django.utils import timezone

from apps.compliance.services import add_months

from .assignments import current_assignment
from .models import Vehicle
from .odometer import record_odometer, void_reading
from .services import VehicleError
from .upkeep import PlanAlertLog, ServicePlan, ServiceRecord
from .usage import OdometerReading

logger = logging.getLogger(__name__)

# These reuse the compliance badge colours (green, amber, red) through the shared status_badge tag.
OK, SOON, OVERDUE = "valid", "due", "expired"
STATE_TEXT = {OK: "On track", SOON: "Due soon", OVERDUE: "Overdue"}
_ORDER = {OVERDUE: 0, SOON: 1, OK: 2}


def _live_records():
    """Prefetch each plan's records, newest first, without cancelled ones. The first is the last service."""
    return Prefetch("records", to_attr="live_records",
                    queryset=ServiceRecord.objects.filter(is_voided=False).order_by("-serviced_on", "-id"))


def plan_status(plan, odometer, today, last=None):
    """Where a plan stands. `last` is its latest service record, or None to count from the plan's baseline."""
    last_on = last.serviced_on if last else plan.baseline_on
    last_km = last.odometer if last else plan.baseline_km
    due_on = add_months(last_on, plan.every_months) if plan.every_months else None
    due_km = last_km + plan.every_km if plan.every_km else None
    days_left = (due_on - today).days if due_on else None
    km_left = due_km - odometer if due_km is not None else None

    if (days_left is not None and days_left < 0) or (km_left is not None and km_left < 0):
        state = OVERDUE
    elif (days_left is not None and days_left <= plan.warn_days) or (km_left is not None and km_left <= plan.warn_km):
        state = SOON
    else:
        state = OK

    parts = []
    if days_left is not None:
        parts.append(f"{-days_left} days overdue" if days_left < 0 else
                     "today" if days_left == 0 else f"in {days_left} days")
    if km_left is not None:
        parts.append(f"{-km_left:,} km over" if km_left < 0 else f"{km_left:,} km left")
    return SimpleNamespace(
        state=state, text=STATE_TEXT[state], summary=" / ".join(parts), due_on=due_on, due_km=due_km,
        days_left=days_left, km_left=km_left, last_on=last_on, last_km=last_km, anchor=last.pk if last else 0)


def _attach(plans, odometer_of, today):
    for p in plans:
        p.status = plan_status(p, odometer_of(p), today, p.live_records[0] if p.live_records else None)
    return sorted(plans, key=lambda p: (_ORDER[p.status.state], p.name))


def vehicle_plans(vehicle, today=None):
    """The vehicle's active plans, most urgent first, each with `.status`."""
    plans = list(vehicle.service_plans.filter(is_active=True).prefetch_related(_live_records()))
    return _attach(plans, lambda p: vehicle.odometer, today or timezone.localdate())


def due_plans(user, today=None):
    """Every active plan on a vehicle the user can see that is due soon or overdue (sold vehicles are skipped)."""
    qs = (ServicePlan.objects.for_user(user).filter(is_active=True).exclude(vehicle__status=Vehicle.Status.SOLD)
          .select_related("vehicle", "vehicle__company").prefetch_related(_live_records()))
    plans = _attach(list(qs), lambda p: p.vehicle.odometer, today or timezone.localdate())
    return [p for p in plans if p.status.state != OK]


# ---------------------------------------------------------------- service records

@transaction.atomic
def add_service(vehicle, *, serviced_on, km, kind=ServiceRecord.Kind.SCHEDULED, plans=(), garage="",
                description="", parts_cost=0, labour_cost=0, invoice_no=""):
    vehicle = Vehicle.objects.select_for_update().get(pk=vehicle.pk)
    if vehicle.status == Vehicle.Status.SOLD:
        raise VehicleError("A sold vehicle cannot take new service entries.")
    if serviced_on > timezone.localdate():
        raise VehicleError("The service date cannot be in the future.")
    if parts_cost < 0 or labour_cost < 0:
        raise VehicleError("Costs cannot be negative.")
    plans = list(plans)
    for plan in plans:
        if plan.vehicle_id != vehicle.pk or not plan.is_active:
            raise VehicleError(f"'{plan.name}' is not an active plan of this vehicle.")
    reading = record_odometer(vehicle, km, serviced_on, source=OdometerReading.Source.SERVICE,
                              note=dict(ServiceRecord.Kind.choices)[kind])
    record = ServiceRecord.objects.create(
        vehicle=vehicle, kind=kind, serviced_on=serviced_on, odometer=km, garage=garage, description=description,
        parts_cost=parts_cost, labour_cost=labour_cost, invoice_no=invoice_no, reading=reading)
    record.plans.set(plans)
    return record


@transaction.atomic
def void_service(record, reason):
    reason = (reason or "").strip()
    if not reason:
        raise VehicleError("Give a reason for cancelling this entry.")
    record = ServiceRecord.objects.select_for_update().get(pk=record.pk)
    if record.is_voided:
        raise VehicleError("This entry is already cancelled.")
    if record.reading_id:
        void_reading(record.reading, f"Service entry cancelled: {reason}")
    record.is_voided, record.voided_reason = True, reason
    record.save()
    return record


# ---------------------------------------------------------------- alerts

def recipients(vehicle, escalate):
    """HR users of the vehicle's company, the current driver, and Management once it is overdue."""
    User = get_user_model()
    groups = ["HR"] + (["Management"] if escalate else [])
    users = User.objects.filter(is_active=True, groups__name__in=groups,
                                company_access__company_id=vehicle.company_id).distinct()
    addresses = {u.email for u in users if u.email}
    holder = current_assignment(vehicle)
    if holder:
        emp = holder.employee
        addresses.add(emp.email or (emp.user.email if emp.user_id else ""))
    return sorted(a for a in addresses if a)


def _link(vehicle):
    try:
        path = reverse("web:vehicle_service", args=[vehicle.pk])
    except NoReverseMatch:
        return ""
    return getattr(settings, "HRMS_BASE_URL", "").rstrip("/") + path


def build_message(plan, status):
    v = plan.vehicle
    name = f"{v.plate_number} ({v.description}): {plan.name}" if v.description else f"{v.plate_number}: {plan.name}"
    word = "is OVERDUE" if status.state == OVERDUE else "is due soon"
    lines = [f"{name} {word}.", ""]
    if status.due_on:
        lines.append(f"Due by date: {status.due_on:%d %b %Y}")
    if status.due_km is not None:
        lines.append(f"Due at: {status.due_km:,} km (odometer now {v.odometer:,} km)")
    holder = current_assignment(v)
    if holder:
        lines.append(f"Driver: {holder.employee.full_name}")
    link = _link(v)
    if link:
        lines += ["", f"Open in Ivhrms: {link}"]
    lines += ["", "This is an automatic reminder from Ivhrms."]
    return f"[Vehicles] {name} {word}", "\n".join(lines)


def run_service_scan(today=None):
    """Email each service plan that has become due soon or overdue, once per service cycle. Safe to re-run."""
    today = today or timezone.localdate()
    stats = {"checked": 0, "alerts": 0, "emails": 0, "errors": 0}
    plans = (ServicePlan.objects.filter(is_active=True, vehicle__company__is_active=True)
             .exclude(vehicle__status=Vehicle.Status.SOLD)
             .select_related("vehicle", "vehicle__company").prefetch_related(_live_records()))
    for plan in plans:
        stats["checked"] += 1
        last = plan.live_records[0] if plan.live_records else None
        status = plan_status(plan, plan.vehicle.odometer, today, last)
        if status.state == OK or plan.alerts.filter(state=status.state, anchor=status.anchor).exists():
            continue
        try:
            with transaction.atomic():
                addresses = recipients(plan.vehicle, escalate=status.state == OVERDUE)
                sent = 0
                if addresses:
                    subject, body = build_message(plan, status)
                    sent = send_mass_mail([(subject, body, None, [a]) for a in addresses])
                PlanAlertLog.objects.create(plan=plan, state=status.state, anchor=status.anchor, recipients=sent)
            stats["alerts"] += 1
            stats["emails"] += sent
        except Exception:                           # one failing plan must not stop the others
            logger.exception("Service alert failed for plan %s", plan.pk)
            stats["errors"] += 1                    # nothing was logged, so the next run retries it
    return stats
