"""Family status requests: HR's list and decisions, and the employee's own read-only "My family" page."""
from pathlib import Path

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Case, IntegerField, Q, Value, When
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.text import slugify
from django.views.decorators.http import require_POST

from apps.audit import services as audit
from apps.compliance.models import Document
from apps.dependants import eligibility
from apps.dependants import status_services as svc
from apps.dependants.models import Dependant, DependantAttachment
from apps.dependants.status import FamilyStatusRequest
from apps.employees.models import Employee
from apps.organization.services import companies_for

from apps.web.access import employee_required, hr_perm
from apps.web.forms.family_status import DeclineForm, StatusRequestForm
from apps.web.views.family_eligibility import _back, _form_page

Status = FamilyStatusRequest.Status
LAYOUTS = {
    "ask": [{"title": "Family status request", "width": "col-lg-6", "rows": [["reason"]]}],
    "decline": [{"title": "Decline the request", "width": "col-lg-6", "rows": [["note"]]}],
}


def _request(request, pk):
    qs = FamilyStatusRequest.objects.for_user(request.user).select_related(
        "employee__reporting_manager", "employee__company", "company", "requested_by")
    return get_object_or_404(qs, pk=pk)


def _url(req):
    return reverse("web:employee_family_status_request", args=[req.pk])


# ------------------------------------------------------------------ HR: the list

@hr_perm("dependants.view_familystatusrequest")
def status_list(request):
    companies = companies_for(request.user)
    base = FamilyStatusRequest.objects.for_user(request.user)
    qs = base.select_related("employee", "company", "requested_by")
    status, q, company = request.GET.get("status", "open"), request.GET.get("q", "").strip(), request.GET.get("company", "")
    if status == "open":
        qs = qs.filter(status__in=FamilyStatusRequest.OPEN)
    elif status in Status.values:
        qs = qs.filter(status=status)
    if company.isdigit():
        qs = qs.filter(company_id=int(company))
    if q:
        qs = qs.filter(Q(employee__first_name__icontains=q) | Q(employee__last_name__icontains=q)
                       | Q(employee__employee_no__icontains=q))
    qs = qs.annotate(rank=Case(When(status=Status.WAITING_HR, then=Value(0)), default=Value(1),
                               output_field=IntegerField())).order_by("rank", "-id")
    params = request.GET.copy()
    params.pop("page", None)
    return render(request, "web/family/status_list.html", {
        "page": Paginator(qs, 25).get_page(request.GET.get("page")), "extra": params.urlencode(),
        "status": status, "q": q, "company": company, "companies": companies,
        "multi_company": companies.count() > 1, "statuses": Status.choices,
        "waiting_hr": base.filter(status=Status.WAITING_HR).count()})


@hr_perm("dependants.view_familystatusrequest")
def status_detail(request, pk):
    req = _request(request, pk)
    user = request.user
    can_change = user.has_perm("dependants.change_familystatusrequest")
    return render(request, "web/family/status_detail.html", {
        "r": req, "progress": svc.progress(req), "manager": req.employee.reporting_manager,
        "can_forward": can_change and req.status == Status.WAITING_HR,
        "can_decline": can_change and req.status == Status.WAITING_HR,
        "can_withdraw": can_change and req.status in FamilyStatusRequest.OPEN})


# ------------------------------------------------------------------ HR: ask, send, decline, withdraw

@hr_perm("dependants.add_familystatusrequest")
def status_add(request, pk):
    employee = get_object_or_404(Employee.objects.for_user(request.user).select_related("company", "designation"),
                                 pk=pk)
    back = _back(request, reverse("web:employee_detail", args=[employee.pk]))
    form = StatusRequestForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            req = svc.ask(employee, requested_by=request.user, reason=form.cleaned_data["reason"])
        except svc.StatusError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "The request was sent to the reporting manager for approval.")
            return redirect(_url(req))
    manager = employee.reporting_manager
    intro = (f"{employee.full_name} ({employee.employee_no}). The request goes straight to "
             f"{manager.full_name if manager else 'the reporting manager'}. When it is approved you can record "
             "the family and ask for the family visa.")
    return _form_page(request, form, f"Request family status: {employee.full_name}", back, LAYOUTS["ask"], intro,
                      "Send to the manager")


@hr_perm("dependants.change_familystatusrequest")
@require_POST
def status_forward(request, pk):
    req = _request(request, pk)
    try:
        svc.forward(req, request.user)
    except svc.StatusError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Sent to the reporting manager for approval.")
    return redirect(_back(request, _url(req)))


@hr_perm("dependants.change_familystatusrequest")
def status_decline(request, pk):
    req = _request(request, pk)
    form = DeclineForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            svc.decline(req, form.cleaned_data["note"])
        except svc.StatusError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "The request was declined. The employee can see the reason.")
            return redirect(_url(req))
    return _form_page(request, form, "Decline the request", _url(req), LAYOUTS["decline"], req.title,
                      "Decline")


@hr_perm("dependants.change_familystatusrequest")
@require_POST
def status_withdraw(request, pk):
    req = _request(request, pk)
    try:
        svc.withdraw(req, request.user, hr=True)
    except svc.StatusError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "The request was withdrawn.")
    return redirect(_url(req))


# ------------------------------------------------------------------ the employee: read-only

@employee_required
def my_family(request):
    emp = request.employee
    members = list(Dependant.objects.filter(employee=emp, is_active=True)
                   .order_by("relationship", "date_of_birth", "id"))
    for m in members:
        m.docs = [d for d in Document.objects.filter(dependant=m, is_current=True)
                  .select_related("document_type").order_by("document_type__name")]
        m.papers = list(m.attachments.all())
    open_req = svc.open_request(emp)
    last = FamilyStatusRequest.objects.filter(employee=emp).exclude(status__in=FamilyStatusRequest.OPEN).first()
    status = eligibility.sponsor_status(emp)
    return render(request, "web/family/my_family.html", {
        "emp": emp, "status": status, "open_req": open_req,
        "progress": svc.progress(open_req) if open_req else [],
        "last": last if (not status.allowed and last and last.status in (Status.DECLINED, Status.REJECTED)) else None,
        "can_request": not status.allowed and open_req is None and emp.status != Employee.Status.SEPARATED,
        "can_withdraw": bool(open_req and open_req.status == Status.WAITING_HR
                             and open_req.requested_by_id == request.user.pk),
        "members": members})


@employee_required
def my_family_request(request):
    emp = request.employee
    form = StatusRequestForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            svc.ask(emp, requested_by=request.user, reason=form.cleaned_data["reason"])
        except svc.StatusError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "Your request was sent to HR.")
            return redirect("web:my_family")
    return _form_page(request, form, "Request family status", reverse("web:my_family"), LAYOUTS["ask"],
                      "HR will review your request and send it to your reporting manager.", "Send to HR")


@employee_required
@require_POST
def my_family_withdraw(request, pk):
    req = get_object_or_404(FamilyStatusRequest, pk=pk, employee=request.employee)
    try:
        svc.withdraw(req, request.user)
    except svc.StatusError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Your request was withdrawn.")
    return redirect("web:my_family")


@employee_required
def my_family_file(request, kind, pk):
    """Open (or, with ?download=1, download) a file of one of the employee's own family members. Nothing here can
    be changed. Every download is written to the audit log."""
    emp = request.employee
    if kind == "doc":
        obj = get_object_or_404(Document.objects.select_related("dependant", "document_type"),
                                pk=pk, dependant__employee=emp, is_current=True)
        fieldfile, label = obj.file, obj.document_type.name
    elif kind == "paper":
        obj = get_object_or_404(DependantAttachment.objects.select_related("dependant"),
                                pk=pk, dependant__employee=emp)
        fieldfile, label = obj.file, obj.get_kind_display()
    else:
        raise Http404
    if not fieldfile:
        raise Http404
    try:
        handle = fieldfile.open("rb")
    except FileNotFoundError:
        raise Http404
    audit.log("download", obj, module="dependants", company_id=emp.company_id)
    name = slugify(f"{obj.dependant.full_name}-{label}") or "file"
    response = FileResponse(handle, as_attachment=request.GET.get("download") == "1",
                            filename=name + Path(fieldfile.name).suffix.lower())
    response["X-Content-Type-Options"] = "nosniff"
    return response
