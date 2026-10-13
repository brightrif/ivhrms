"""Air ticket requests: ask, get approved (through the approval engine), issue the tickets or pay the allowance,
confirm the travel. Each step closes or leaves open the employee's "next ticket" (the entitlement)."""
from decimal import Decimal

from django.contrib.contenttypes.models import ContentType
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.core import approvals
from apps.core.models import ApprovalRequest
from apps.dependants.models import Dependant
from apps.employees.models import Employee

from . import services
from .defaults import FLOW_CODE
from .models import TicketEntitlement, TicketPolicy
from .ticketrequests import TicketRequest, TicketTraveller

Status = TicketRequest.Status
Ent = TicketEntitlement.Status
ZERO = Decimal("0")


class RequestError(Exception):
    pass


def eligible_dependants(employee, entitlement):
    """The family the policy covers: the spouse if it says so, children if it covers any. Others can be on the
    list, but a request is refused if it names more than the policy allows."""
    relations = []
    if entitlement.spouse_covered:
        relations.append(Dependant.Relationship.SPOUSE)
    if entitlement.max_children:
        relations.append(Dependant.Relationship.CHILD)
    return (Dependant.objects.filter(employee=employee, is_active=True, relationship__in=relations)
            .order_by("relationship", "date_of_birth", "id"))


def open_request(employee):
    return TicketRequest.objects.filter(employee=employee, status__in=TicketRequest.OPEN).first()


@transaction.atomic
def request_ticket(employee, *, depart_on, return_on=None, destination="", dependants=(), notes="", requested_by):
    employee = Employee.objects.select_related("company", "grade").get(pk=employee.pk)
    if employee.worker_type != Employee.WorkerType.STAFF:
        raise RequestError("Air tickets are for staff.")
    if employee.status == Employee.Status.SEPARATED:
        raise RequestError(f"{employee.full_name} has left the company.")
    entitlement = services.sync_entitlement(employee)
    if entitlement is None:
        raise RequestError(f"No ticket policy applies to {employee.full_name}, so there is no air ticket to request.")
    entitlement = TicketEntitlement.objects.select_for_update().get(pk=entitlement.pk)
    if open_request(employee) is not None:
        raise RequestError("There is already an air ticket request in progress. Wait for it, or withdraw it first.")
    if depart_on < timezone.localdate():
        raise RequestError("The departure date cannot be in the past.")
    if depart_on < entitlement.eligible_from:
        raise RequestError(f"The next ticket is due from {entitlement.eligible_from:%d %b %Y}. "
                           "Choose a departure on or after that day.")
    if return_on is not None and return_on <= depart_on:
        raise RequestError("The return date must be after the departure.")

    family = list(dict.fromkeys(dependants))
    covered = {d.pk for d in eligible_dependants(employee, entitlement)}
    for d in family:
        if d.pk not in covered:
            raise RequestError(f"{d.full_name} is not covered by this ticket policy.")
    if sum(d.relationship == Dependant.Relationship.SPOUSE for d in family) > 1:
        raise RequestError("Only one spouse can be covered.")
    if sum(d.relationship == Dependant.Relationship.CHILD for d in family) > entitlement.max_children:
        raise RequestError(f"The policy covers up to {entitlement.max_children} "
                           f"child{'ren' if entitlement.max_children != 1 else ''}.")

    allowance_total = None
    if entitlement.mode == TicketPolicy.Mode.ALLOWANCE:
        if not entitlement.allowance_amount:
            raise RequestError("This policy pays an allowance but no amount is set. Ask HR to fix the policy.")
        allowance_total = entitlement.allowance_amount * (1 + len(family))
    try:
        req = TicketRequest.objects.create(
            company_id=employee.company_id, employee=employee, entitlement=entitlement, mode=entitlement.mode,
            depart_on=depart_on, return_on=return_on, destination=(destination or "").strip(),
            notes=(notes or "").strip(), requested_by=requested_by, allowance_total=allowance_total)
    except IntegrityError as exc:
        raise RequestError("There is already an air ticket request in progress.") from exc
    TicketTraveller.objects.create(request=req, name=employee.full_name, relationship="employee")
    for d in family:
        TicketTraveller.objects.create(request=req, dependant=d, name=d.full_name, relationship=d.relationship)
    try:
        approvals.submit(FLOW_CODE, req, employee=employee, requested_by=requested_by)
    except approvals.ApprovalError as exc:
        raise RequestError(str(exc)) from exc               # raising inside atomic() rolls the request back
    return req


@transaction.atomic
def finalize(approval):
    """Called when the approval finishes, whatever the outcome. Idempotent."""
    req = TicketRequest.objects.select_for_update().get(pk=approval.object_id)
    if req.status != Status.PENDING:
        return req
    req.status = {ApprovalRequest.Status.APPROVED: Status.APPROVED,
                  ApprovalRequest.Status.REJECTED: Status.REJECTED}.get(approval.status, Status.CANCELLED)
    req.save(update_fields=["status", "updated_at"])
    return req


@transaction.atomic
def withdraw(request, user):
    """The person who asked takes the request back while it is still waiting for approval."""
    ct = ContentType.objects.get_for_model(TicketRequest)
    approval = ApprovalRequest.objects.filter(content_type=ct, object_id=str(request.pk),
                                              status=ApprovalRequest.Status.PENDING).first()
    if approval is None:
        raise RequestError("This request is not waiting for approval.")
    try:
        approvals.cancel(approval.pk, user)                  # finishes the approval, which cancels the request
    except approvals.ApprovalError as exc:
        raise RequestError(str(exc)) from exc
    return TicketRequest.objects.get(pk=request.pk)


def _lock(request):
    return TicketRequest.objects.select_for_update().select_related("employee", "entitlement").get(pk=request.pk)


@transaction.atomic
def issue(request, user, *, airline="", booking_ref="", issued_on, tickets, employee_share=ZERO):
    """The company has bought the tickets. `tickets` is {traveller id: (ticket number, fare)}: every traveller needs
    both, because the cost is what the reports add up."""
    req = _lock(request)
    if req.mode != TicketPolicy.Mode.COMPANY:
        raise RequestError("This request is paid as an allowance, not booked by the company.")
    if req.status != Status.APPROVED:
        raise RequestError("Only an approved request can have its tickets issued.")
    if issued_on > timezone.localdate():
        raise RequestError("The issue date cannot be in the future.")
    travellers = list(req.travellers.select_for_update())
    for t in travellers:
        ticket_no, fare = tickets.get(t.pk, ("", None))
        if not (ticket_no or "").strip() or fare is None:
            raise RequestError(f"Enter the ticket number and the fare for {t.name}.")
        if fare < 0:
            raise RequestError("A fare cannot be negative.")
        t.ticket_no, t.fare = ticket_no.strip(), fare
        t.save(update_fields=["ticket_no", "fare", "updated_at"])
    employee_share = employee_share or ZERO
    if employee_share < 0 or employee_share > sum((t.fare for t in travellers), ZERO):
        raise RequestError("The employee's share cannot be negative or more than the fares.")
    req.airline, req.booking_ref, req.issued_on = (airline or "").strip(), (booking_ref or "").strip(), issued_on
    req.employee_share, req.status = employee_share, Status.BOOKED
    req.save(update_fields=["airline", "booking_ref", "issued_on", "employee_share", "status", "updated_at"])
    return req


def _close_entitlement(req, *, status, used_on, cost):
    ent = TicketEntitlement.objects.select_for_update().get(pk=req.entitlement_id)
    if ent.status != Ent.OPEN:
        raise RequestError("This ticket was already marked as taken (perhaps recorded by hand), "
                           "so this request cannot close it again.")
    ent.status, ent.used_on, ent.cost = status, used_on, cost
    ent.destination, ent.notes = req.destination, f"Request #{req.pk}"
    ent.save()
    services.sync_entitlement(req.employee)                  # works out the next ticket from this date


@transaction.atomic
def mark_travelled(request, *, departed_on, returned_on=None):
    req = _lock(request)
    if req.status != Status.BOOKED:
        raise RequestError("Only a request whose tickets are issued can be marked as travelled.")
    if departed_on > timezone.localdate():
        raise RequestError("The departure date cannot be in the future. Mark it once they have travelled.")
    if returned_on is not None and returned_on < departed_on:
        raise RequestError("The return date cannot be before the departure.")
    _close_entitlement(req, status=Ent.USED, used_on=departed_on, cost=req.company_cost)
    req.status, req.departed_on, req.returned_on = Status.COMPLETED, departed_on, returned_on
    req.save(update_fields=["status", "departed_on", "returned_on", "updated_at"])
    return req


@transaction.atomic
def pay_allowance(request, *, paid_on, amount=None, reference=""):
    """The allowance has been paid (or handed to payroll). Closes the entitlement as 'paid'."""
    req = _lock(request)
    if req.mode != TicketPolicy.Mode.ALLOWANCE:
        raise RequestError("This request is booked by the company, not paid as an allowance.")
    if req.status != Status.APPROVED:
        raise RequestError("Only an approved request can be marked as paid.")
    if paid_on > timezone.localdate():
        raise RequestError("The payment date cannot be in the future.")
    amount = req.allowance_total if amount is None else amount
    if not amount or amount <= 0:
        raise RequestError("Enter the amount paid.")
    _close_entitlement(req, status=Ent.PAID, used_on=paid_on, cost=amount)
    req.status, req.paid_on, req.payment_ref = Status.PAID, paid_on, (reference or "").strip()
    req.save(update_fields=["status", "paid_on", "payment_ref", "updated_at"])
    return req


@transaction.atomic
def cancel(request, user, *, reason):
    """HR cancels an approved request or issued tickets. The entitlement stays open: the employee can ask again."""
    req = _lock(request)
    if req.status not in (Status.APPROVED, Status.BOOKED):
        raise RequestError("Only an approved request, or one whose tickets are issued, can be cancelled.")
    if not (reason or "").strip():
        raise RequestError("Give the reason for cancelling.")
    req.status, req.cancel_reason = Status.CANCELLED, reason.strip()
    req.save(update_fields=["status", "cancel_reason", "updated_at"])
    return req
