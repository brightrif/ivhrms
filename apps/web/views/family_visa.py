"""Family visa requests: ask, follow the approval, process, pay, and the cost report (HR and Finance)."""
from pathlib import Path

from django.contrib import messages
from django.contrib.contenttypes.models import ContentType
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify
from django.views.decorators.http import require_POST

from apps.audit import services as audit
from apps.core.models import ApprovalRequest
from apps.dependants import visa_services as visa
from apps.dependants.visa import FamilyVisaRequest, VisaPayment
from apps.organization.services import companies_for

from apps.web.access import hr_perm
from apps.web.forms.family_visa import CompleteForm, PaymentForm, RequestForm, SubmittedForm
from apps.web.views.family import _dependant, _detail, _who

Status = FamilyVisaRequest.Status
LAYOUTS = {
    "request": [{"title": "Request", "width": "col-lg-7", "rows": [["kind"], ["reason"]]}],
    "submitted": [{"title": "Submitted to the authority", "width": "col-lg-6", "rows": [["submitted_on", "reference"]]}],
    "complete": [{"title": "The visa", "width": "col-lg-7",
                  "rows": [["number"], ["issue_date", "expiry_date"], ["completed_on"]]}],
    "complete_cancel": [{"title": "Cancellation", "width": "col-lg-5", "rows": [["completed_on"]]}],
    "payment": [{"title": "Payment", "width": "col-lg-7",
                 "rows": [["description", "paid_on"], ["government_fee", "service_fee", "fine"], ["reference"]]},
                {"title": "Who pays", "width": "col-lg-5", "rows": [["receipt"], ["charged_to_employee"], ["remarks"]]}],
}


def _form_page(request, form, title, back_url, layout, intro="", submit_label="Save"):
    return render(request, "web/form_layout.html", {
        "form": form, "title": title, "back_url": back_url, "layout": LAYOUTS[layout], "intro": intro,
        "submit_label": submit_label})


def _visa_request(request, pk):
    qs = (FamilyVisaRequest.objects.for_user(request.user)
          .select_related("dependant", "employee", "company", "requested_by", "visa_document"))
    return get_object_or_404(qs, pk=pk)


def _url(req):
    return reverse("web:employee_family_request", args=[req.pk])


def _trail(req):
    """The approval steps and who did what, for the page."""
    ct = ContentType.objects.get_for_model(FamilyVisaRequest)
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


# ------------------------------------------------------------------ list and report

@hr_perm("dependants.view_familyvisarequest")
def family_requests(request):
    companies = companies_for(request.user)
    qs = (FamilyVisaRequest.objects.for_user(request.user)
          .select_related("dependant", "employee", "company", "requested_by"))
    status, kind = request.GET.get("status", "open"), request.GET.get("kind", "")
    q, company = request.GET.get("q", "").strip(), request.GET.get("company", "")
    if status == "open":
        qs = qs.filter(status__in=FamilyVisaRequest.OPEN)
    elif status in Status.values:
        qs = qs.filter(status=status)
    if kind in FamilyVisaRequest.Kind.values:
        qs = qs.filter(kind=kind)
    if company.isdigit():
        qs = qs.filter(company_id=int(company))
    if q:
        qs = qs.filter(Q(dependant__first_name__icontains=q) | Q(dependant__last_name__icontains=q)
                       | Q(employee__first_name__icontains=q) | Q(employee__last_name__icontains=q)
                       | Q(employee__employee_no__icontains=q))
    params = request.GET.copy()
    params.pop("page", None)
    return render(request, "web/family/requests.html", {
        "page": Paginator(qs, 25).get_page(request.GET.get("page")), "extra": params.urlencode(),
        "status": status, "kind": kind, "q": q, "company": company, "companies": companies,
        "multi_company": companies.count() > 1, "statuses": Status.choices,
        "kinds": FamilyVisaRequest.Kind.choices})


@hr_perm("dependants.view_visapayment")
def family_costs(request):
    years = visa.payment_years(request.user)
    try:
        year = int(request.GET.get("year", ""))
    except ValueError:
        year = years[0] if years else timezone.localdate().year
    return render(request, "web/family/costs.html", {
        "year": year, "years": years or [year], **visa.cost_report(request.user, year)})


# ------------------------------------------------------------------ one request

@hr_perm("dependants.add_familyvisarequest")
def family_request_add(request, pk):
    dep = _dependant(request, pk)
    back = _detail(dep)
    kinds = visa.allowed_kinds(dep)
    if not kinds:
        messages.error(request, visa.blocked_reason(dep) or f"There is nothing to request for {dep.full_name} now.")
        return redirect(back)
    initial = {"kind": request.GET.get("kind")} if request.GET.get("kind") in [k.value for k in kinds] else {}
    form = RequestForm(request.POST or None, kinds=kinds, initial=initial)
    if request.method == "POST" and form.is_valid():
        try:
            req = visa.request_visa(dep, form.cleaned_data["kind"], requested_by=request.user,
                                    reason=form.cleaned_data["reason"])
        except visa.VisaError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "The request was sent for approval.")
            return redirect(_url(req))
    return _form_page(request, form, f"Visa request: {dep.full_name}", back, "request", intro=_who(dep),
                      submit_label="Send for approval")


@hr_perm("dependants.view_familyvisarequest")
def family_request(request, pk):
    req = _visa_request(request, pk)
    user = request.user
    payments = list(req.payments.all())
    can_change = user.has_perm("dependants.change_familyvisarequest")
    return render(request, "web/family/request.html", {
        "r": req, "dep": req.dependant, "trail": _trail(req), "payments": payments,
        "paid_total": sum((p.total for p in payments), 0), "charged_total": sum((p.total for p in payments if p.charged_to_employee), 0),
        "checklist": visa.checklist(req.dependant) if req.kind != req.Kind.CANCELLATION else [],
        "can_submit": can_change and req.status == Status.APPROVED,
        "can_complete": can_change and req.status in (Status.APPROVED, Status.SUBMITTED),
        "can_withdraw": req.status == Status.PENDING and user.id == req.requested_by_id,
        "can_pay": user.has_perm("dependants.add_visapayment")
                   and req.status in (Status.APPROVED, Status.SUBMITTED, Status.COMPLETED),
        "can_see_costs": user.has_perm("dependants.view_visapayment")})


@hr_perm("dependants.add_familyvisarequest")
@require_POST
def family_request_withdraw(request, pk):
    req = _visa_request(request, pk)
    try:
        visa.cancel_request(req, request.user)
    except visa.VisaError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "The request was withdrawn.")
    return redirect(_url(req))


@hr_perm("dependants.change_familyvisarequest")
def family_request_submitted(request, pk):
    req = _visa_request(request, pk)
    form = SubmittedForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            visa.mark_submitted(req, **form.cleaned_data)
        except visa.VisaError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "Marked as submitted to the authority.")
            return redirect(_url(req))
    return _form_page(request, form, "Mark as submitted", _url(req), "submitted", intro=req.title)


@hr_perm("dependants.change_familyvisarequest")
def family_request_complete(request, pk):
    req = _visa_request(request, pk)
    form = CompleteForm(request.POST or None, request=req)
    if request.method == "POST" and form.is_valid():
        try:
            visa.complete(req, request.user, **form.cleaned_data)
        except visa.VisaError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "The request is complete."
                             + (" The visa is now tracked with the other documents."
                                if req.kind != req.Kind.CANCELLATION else
                                f" {req.dependant.full_name} is marked as no longer sponsored."))
            return redirect(_url(req))
    cancelling = req.kind == req.Kind.CANCELLATION
    return _form_page(request, form, "Record the cancellation" if cancelling else "Record the issued visa",
                      _url(req), "complete_cancel" if cancelling else "complete", intro=req.title,
                      submit_label="Complete the request")


@hr_perm("dependants.add_visapayment")
def family_request_payment(request, pk):
    req = _visa_request(request, pk)
    form = PaymentForm(request.POST or None, request.FILES or None, instance=VisaPayment(request=req))
    if request.method == "POST" and form.is_valid():
        try:
            visa.add_payment(req, form.save(commit=False))
        except visa.VisaError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "Payment recorded.")
            return redirect(_url(req))
    return _form_page(request, form, "Record a payment", _url(req), "payment", intro=req.title)


@hr_perm("dependants.view_visapayment")
def family_receipt(request, pk):
    """Open (or, with ?download=1, download) a payment receipt. Checked, and written to the audit log."""
    payment = get_object_or_404(VisaPayment.objects.for_user(request.user).select_related("request__dependant"), pk=pk)
    if not payment.receipt:
        raise Http404
    try:
        handle = payment.receipt.open("rb")
    except FileNotFoundError:
        raise Http404
    audit.log("download", payment, module="dependants", company_id=payment.company_id)
    name = slugify(f"receipt-{payment.request.dependant.full_name}-{payment.paid_on:%Y-%m-%d}") or "receipt"
    response = FileResponse(handle, as_attachment=request.GET.get("download") == "1",
                            filename=name + Path(payment.receipt.name).suffix.lower())
    response["X-Content-Type-Options"] = "nosniff"
    return response
