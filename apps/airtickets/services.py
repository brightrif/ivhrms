"""Air tickets: which policy fits an employee, when their next ticket is due, and recording tickets already taken."""
import calendar
from collections import Counter
from datetime import date

from django.db import transaction
from django.db.models import Max, ProtectedError
from django.utils import timezone

from apps.employees.models import Employee

from .models import TicketEntitlement, TicketPolicy
from .nationalities import canonical

Status = TicketEntitlement.Status


class TicketError(Exception):
    pass


def add_months(d, months):
    index = d.month - 1 + months
    year, month = d.year + index // 12, index % 12 + 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def staff():
    """The people air tickets are worked out for: staff who have not left. (Labor workers are not included.)"""
    return Employee.objects.filter(worker_type=Employee.WorkerType.STAFF).exclude(status=Employee.Status.SEPARATED)


def _policies(company_id):
    return list(TicketPolicy.objects.filter(company_id=company_id, is_active=True).select_related("min_grade"))


def policy_for(employee, policies=None):
    """The policy that applies: of those that fit, the most specific, then the higher priority, then the newer."""
    if policies is None:
        policies = _policies(employee.company_id)
    fitting = [p for p in policies if not p.mismatches(employee)]
    return max(fitting, key=lambda p: (p.specificity, p.priority, p.pk), default=None)


def eligible_from_for(employee, policy):
    """First ticket: after the policy's months of service. After that: the interval after the last ticket was taken
    (or, for one that was waived, after the date it fell due)."""
    last = employee.ticket_entitlements.exclude(status=Status.OPEN).order_by("-cycle").first()
    if last is None:
        return add_months(employee.joining_date, policy.first_after_months)
    return add_months(last.used_on or last.eligible_from, policy.interval_months)


@transaction.atomic
def sync_entitlement(employee, policies=None):
    """Make the employee's one open 'next ticket' match the policy that applies to them today. Safe to repeat."""
    if employee.worker_type != Employee.WorkerType.STAFF or employee.status == Employee.Status.SEPARATED:
        return None
    if policies is None:
        policies = _policies(employee.company_id)
    policy = policy_for(employee, policies)
    current = employee.ticket_entitlements.select_for_update().filter(status=Status.OPEN).first()
    if policy is None:
        if current is not None:
            try:
                current.delete()                    # worked-out data, not history: it is rebuilt if a policy returns
            except ProtectedError:
                pass
        return None
    terms = {"policy": policy, "mode": policy.mode, "allowance_amount": policy.allowance_amount,
             "spouse_covered": policy.spouse_covered, "max_children": policy.max_children,
             "eligible_from": eligible_from_for(employee, policy)}
    if current is not None:
        changed = {name: value for name, value in terms.items() if getattr(current, name) != value}
        if changed:
            for name, value in changed.items():
                setattr(current, name, value)
            current.save()
        return current
    cycle = (employee.ticket_entitlements.aggregate(top=Max("cycle"))["top"] or 0) + 1
    return TicketEntitlement.objects.create(company_id=employee.company_id, employee=employee, cycle=cycle,
                                            status=Status.OPEN, **terms)


def sync_all(companies=None):
    """Do it for everyone (or for the given companies). Returns how many were checked and how many have a policy."""
    stats = {"checked": 0, "with_policy": 0, "without_policy": 0}
    people = staff().select_related("company", "grade")
    if companies is not None:
        people = people.filter(company__in=companies)
    by_company = {}
    for employee in people:
        policies = by_company.setdefault(employee.company_id, _policies(employee.company_id))
        stats["checked"] += 1
        stats["with_policy" if sync_entitlement(employee, policies) else "without_policy"] += 1
    return stats


@transaction.atomic
def record_past_ticket(employee, *, travel_date, destination="", cost=None, notes=""):
    """A ticket already taken (history when the system starts, or one booked outside it). Enter them oldest first:
    each one closes the open entitlement, and the next is worked out from the travel date."""
    employee = Employee.objects.select_related("company", "grade").get(pk=employee.pk)
    if travel_date > timezone.localdate():
        raise TicketError("This date is in the future. Only tickets already taken are recorded here.")
    if travel_date < employee.joining_date:
        raise TicketError(f"The travel date is before {employee.full_name} joined ({employee.joining_date:%d %b %Y}).")
    if cost is not None and cost < 0:
        raise TicketError("The cost cannot be negative.")
    current = sync_entitlement(employee)
    if current is None:
        raise TicketError(f"No ticket policy applies to {employee.full_name}, so there is no entitlement to record "
                          "this against. Add or fix a policy first.")
    last = employee.ticket_entitlements.filter(status__in=(Status.USED, Status.PAID)).order_by("-used_on").first()
    if last is not None and last.used_on and travel_date <= last.used_on:
        raise TicketError(f"Enter tickets oldest first: the last one recorded was on {last.used_on:%d %b %Y}.")
    current.status, current.used_on = Status.USED, travel_date
    current.destination, current.cost, current.notes = (destination or "").strip(), cost, (notes or "").strip()
    current.save()
    sync_entitlement(employee)
    return current


def entitlement_state(entitlement, today=None):
    """('eligible', days overdue) or ('upcoming', days to go) for an open entitlement."""
    days = ((today or timezone.localdate()) - entitlement.eligible_from).days
    return ("eligible", days) if days >= 0 else ("upcoming", -days)


def nationality_problems(employees):
    """(how many have no nationality, [(text, how many)] that are not in the list), for the staff given."""
    blank, unknown = 0, Counter()
    for text in employees.values_list("nationality", flat=True):
        text = (text or "").strip()
        if not text:
            blank += 1
        elif canonical(text) is None:
            unknown[text] += 1
    return blank, sorted(unknown.items())
