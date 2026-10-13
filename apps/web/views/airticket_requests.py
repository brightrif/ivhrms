"""Air ticket requests: HR pages (ask on someone's behalf, issue, travel, allowance, cancel) and the employee's own
page (ask, follow, withdraw)."""
from decimal import Decimal

from django.contrib import messages
from django.contrib.contenttypes.models import ContentType
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.airtickets import request_services as tickets
from apps.airtickets import services
from apps.airtickets.models import TicketEntitlement
from apps.airtickets.ticketrequests import TicketRequest
from apps.core.models import ApprovalRequest
from apps.organization.services import companies_for

from apps.web.access import employee_required, hr_perm
from apps.web.forms.airticket_requests import CancelForm, IssueForm, PaidForm, TicketRequestForm, TravelledForm
from apps.web.views.airtickets import _detail, _employee

Status = TicketRequest.Status

LAYOUTS = {
    "request": [{"title": "The trip", "width": "col-lg-6",
                 "rows": [["depart_on", "return_on"], ["destination"], ["notes"]]},
                {"title": "Who travels", "width": "col-lg-6", "rows": [["travellers"]]}],
    "travelled": [{"title": "The travel", "width": "col-lg-6", "rows": [["departed_on", "returned_on"]]}],
    "paid": [{"title": "The allowance", "width": "col-lg-6", "rows": [["paid_on", "amount"], ["reference"]]}],
    "cancel": [{"title": "Cancel", "width": "col-lg-6", "rows": [["reason"]]}],
}


def _form_page(request, form, title, back_url, layout, intro="", submit_label="Save"):
    return render(request, "web/form_layout.html", {
        "form": form, "title": title, "back_url": back_url,
        "layout": LAYOUTS[layout] if isinstance(layout, str) else layout, "intro": intro,
        "submit_label": submit_label})


def _req(request, pk):
    qs = (TicketRequest.objects.for_user(request.user)
          .select_related("employee", "company", "requested_by", "entitlement"))
    return get_object_or_404(qs, pk=pk)


def _url(req):
    return reverse("web:employee_tickets_request", args=[req.pk])


def _trail(req):
    """The approval steps and who did what, for the page."""
    ct = ContentType.objects.get_for_model(TicketRequest)
    approval = (ApprovalRequest.objects.filter(content_type=ct, object_id=str(req.pk))
                .select_related("flow").order_by("-id").first())
    if approval is None:
        return []
    done = {a.step_id: a for a in approval.actions.select_related("actor")}
    rows = []
    for step in approval.flow.steps.all():
        action = done.get(step.pk)
        if action:
            state = "approved" if action.decision == "approve" else "rejected"
        elif approval.status == ApprovalRequest.Status.PENDING:
            state = "waiting" if approval.current_step == step.order else "later"
        else:
            state = "not reached"
        rows.append({"step": step, "action": action, "state": state})
    return rows


# ------------------------------------------------------------------ HR: the list and one request

@hr_perm("airtickets.view_ticketrequest")
def requests_list(request):
    companies = companies_for(request.user)
    qs = (TicketRequest.objects.for_user(request.user)
          .select_related("employee", "company", "requested_by").prefetch_related("travellers"))
    status, q, company = request.GET.get("status", "open"), request.GET.get("q", "").strip(), request.GET.get("company", "")
    if status == "open":
        qs = qs.filter(status__in=TicketRequest.OPEN)
    elif status in Status.values:
        qs = qs.filter(status=status)
    if company.isdigit():
        qs = qs.filter(company_id=int(company))
    if q:
        qs = qs.filter(Q(employee__first_name__icontains=q) | Q(employee__last_name__icontains=q)
                       | Q(employee__employee_no__icontains=q))
    params = request.GET.copy()
    params.pop("page", None)
    return render(request, "web/airtickets/requests.html", {
        "page": Paginator(qs, 25).get_page(request.GET.get("page")), "extra": params.urlencode(),
        "status": status, "q": q, "company": company, "companies": companies,
        "multi_company": companies.count() > 1, "statuses": Status.choices})


@hr_perm("airtickets.view_ticketrequest")
def request_detail(request, pk):
    req = _req(request, pk)
    user = request.user
    change = user.has_perm("airtickets.change_ticketrequest")
    return render(request, "web/airtickets/request.html", {
        "r": req, "emp": req.employee, "trail": _trail(req), "travellers": list(req.travellers.all()),
        "can_issue": change and req.status == Status.APPROVED and req.mode == "company",
        "can_pay": change and req.status == Status.APPROVED and req.mode == "allowance",
        "can_travelled": change and req.status == Status.BOOKED,
        "can_cancel": change and req.status in (Status.APPROVED, Status.BOOKED),
        "can_withdraw": req.status == Status.PENDING and user.id == req.requested_by_id})


@hr_perm("airtickets.add_ticketrequest")
def request_add(request, pk):
    """HR asks on an employee's behalf. The HR person cannot then give the HR approval themselves: another HR user
    does that step (employees can also ask for themselves, on their own page)."""
    emp = _employee(request, pk)
    entitlement = services.sync_entitlement(emp)
    if entitlement is None:
        messages.error(request, f"No ticket policy applies to {emp.full_name}.")
        return redirect(_detail(emp))
    form = TicketRequestForm(request.POST or None, employee=emp, entitlement=entitlement)
    if request.method == "POST" and form.is_valid():
        cd = form.cleaned_data
        try:
            req = tickets.request_ticket(emp, depart_on=cd["depart_on"], return_on=cd["return_on"],
                                         destination=cd["destination"], dependants=cd["travellers"],
                                         notes=cd["notes"], requested_by=request.user)
        except tickets.RequestError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "The request was sent for approval.")
            return redirect(_url(req))
    return _form_page(request, form, f"Request an air ticket: {emp.full_name}", _detail(emp), "request",
                      intro=f"The next ticket is due from {entitlement.eligible_from:%d %b %Y}.",
                      submit_label="Send for approval")


@hr_perm("airtickets.add_ticketrequest")
@require_POST
def request_withdraw(request, pk):
    req = _req(request, pk)
    try:
        tickets.withdraw(req, request.user)
    except tickets.RequestError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "The request was withdrawn.")
    return redirect(_url(req))


@hr_perm("airtickets.change_ticketrequest")
def request_issue(request, pk):
    req = _req(request, pk)
    form = IssueForm(request.POST or None, request=req)
    if request.method == "POST" and form.is_valid():
        cd = form.cleaned_data
        try:
            tickets.issue(req, request.user, airline=cd["airline"], booking_ref=cd["booking_ref"],
                          issued_on=cd["issued_on"], tickets=form.tickets(),
                          employee_share=cd["employee_share"] or Decimal("0"))
        except tickets.RequestError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "The tickets are recorded as issued.")
            return redirect(_url(req))
    rows = [["airline", "booking_ref"], ["issued_on", "employee_share"]]
    people = [{"title": t.name, "width": "col-lg-4", "rows": [[f"ticket_{t.pk}"], [f"fare_{t.pk}"]]}
              for t in form.travellers]
    layout = [{"title": "The booking", "width": "col-lg-4", "rows": rows}] + people
    return _form_page(request, form, "Issue the tickets", _url(req), layout, intro=req.title,
                      submit_label="Record the tickets")


@hr_perm("airtickets.change_ticketrequest")
def request_travelled(request, pk):
    req = _req(request, pk)
    form = TravelledForm(request.POST or None, request=req)
    if request.method == "POST" and form.is_valid():
        try:
            tickets.mark_travelled(req, **form.cleaned_data)
        except tickets.RequestError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "Marked as travelled. The next ticket has been worked out from that date.")
            return redirect(_url(req))
    return _form_page(request, form, "Mark as travelled", _url(req), "travelled", intro=req.title)


@hr_perm("airtickets.change_ticketrequest")
def request_paid(request, pk):
    req = _req(request, pk)
    form = PaidForm(request.POST or None, request=req)
    if request.method == "POST" and form.is_valid():
        cd = form.cleaned_data
        try:
            tickets.pay_allowance(req, paid_on=cd["paid_on"], amount=cd["amount"], reference=cd["reference"])
        except tickets.RequestError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "Allowance recorded as paid. The next ticket has been worked out from that date.")
            return redirect(_url(req))
    return _form_page(request, form, "Record the allowance paid", _url(req), "paid", intro=req.title)


@hr_perm("airtickets.change_ticketrequest")
def request_cancel(request, pk):
    req = _req(request, pk)
    form = CancelForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            tickets.cancel(req, request.user, reason=form.cleaned_data["reason"])
        except tickets.RequestError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "The request was cancelled. The employee can ask again.")
            return redirect(_url(req))
    return _form_page(request, form, "Cancel this request", _url(req), "cancel", intro=req.title,
                      submit_label="Cancel the request")


# ------------------------------------------------------------------ the employee's own page

@employee_required
def my_tickets(request):
    emp = request.employee
    current = services.sync_entitlement(emp)
    mine = list(TicketRequest.objects.filter(employee=emp).prefetch_related("travellers")[:20])
    return render(request, "web/airtickets/my.html", {
        "emp": emp, "current": current, "state": services.entitlement_state(current) if current else None,
        "requests": mine, "open": next((r for r in mine if r.status in TicketRequest.OPEN), None),
        "profile": getattr(emp, "travel_profile", None)})


@employee_required
def my_ticket_apply(request):
    emp = request.employee
    entitlement = services.sync_entitlement(emp)
    if entitlement is None:
        messages.error(request, "No air ticket policy applies to you. Please ask HR.")
        return redirect("web:my_tickets")
    form = TicketRequestForm(request.POST or None, employee=emp, entitlement=entitlement)
    if request.method == "POST" and form.is_valid():
        cd = form.cleaned_data
        try:
            tickets.request_ticket(emp, depart_on=cd["depart_on"], return_on=cd["return_on"],
                                   destination=cd["destination"], dependants=cd["travellers"], notes=cd["notes"],
                                   requested_by=request.user)
        except tickets.RequestError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "Your request was sent to your manager for approval.")
            return redirect("web:my_tickets")
    return _form_page(request, form, "Request an air ticket", reverse("web:my_tickets"), "request",
                      intro=f"Your next ticket is due from {entitlement.eligible_from:%d %b %Y}.",
                      submit_label="Send for approval")


@employee_required
@require_POST
def my_ticket_withdraw(request, pk):
    req = get_object_or_404(TicketRequest, pk=pk, employee=request.employee)
    try:
        tickets.withdraw(req, request.user)
    except tickets.RequestError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Your request was withdrawn.")
    return redirect("web:my_tickets")
