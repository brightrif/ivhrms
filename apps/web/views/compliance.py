from datetime import date
from pathlib import Path

from django.contrib import messages
from django.db.models import Q
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify
from django.views.decorators.http import require_POST

from apps.audit import services as audit
from apps.compliance import schedule, services
from apps.compliance.models import Document, DocumentType, RenewalPayment, RenewalTask
from apps.organization.services import companies_for

from apps.web.forms.compliance import (
    DocumentForm,
    DocumentTypeForm,
    PaymentForm,
    RenewalTaskForm,
    RenewForm,
)
from apps.web.access import hr_perm

Company_types = DocumentType.AppliesTo.COMPANY

# Full-page layouts: two columns on a laptop screen so nothing needs scrolling. See form_layout.html.
LAYOUTS = {
    "document": [
        {"title": "Document", "width": "col-lg-7",
         "rows": [["company", "document_type"], ["reference_name", "number"], ["issue_date", "expiry_date"]]},
        {"title": "Handling", "width": "col-lg-5",
         "rows": [["responsible", "agent_name"], ["file"], ["notes"]]},
    ],
    "renew": [
        {"title": "New certificate", "width": "col-lg-6",
         "rows": [["number"], ["issue_date", "expiry_date"]]},
        {"title": "Scan and notes", "width": "col-lg-6", "rows": [["file"], ["notes"]]},
    ],
    "task": [
        {"title": "Renewal", "width": "col-xl-8",
         "rows": [["assignee", "due_date", "estimated_cost"], ["notes"]]},
    ],
    "payment": [
        {"title": "Payment", "width": "col-lg-7",
         "rows": [["description", "paid_on"], ["government_fee", "service_fee", "fine"], ["method", "reference"]]},
        {"title": "Receipt", "width": "col-lg-5", "rows": [["receipt"], ["paid_by"], ["remarks"]]},
    ],
    "type": [
        {"title": "Document type", "width": "col-lg-6",
         "rows": [["name", "name_ar"], ["code", "authority"]]},
        {"title": "Validity and alerts", "width": "col-lg-6",
         "rows": [["default_validity_months", "overdue_repeat_days"], ["alert_days"], ["is_mandatory"]]},
    ],
}


def _form_page(request, form, title, back_url, layout, intro=""):
    return render(request, "web/form_layout.html", {
        "form": form, "title": title, "back_url": back_url, "layout": LAYOUTS[layout], "intro": intro})


def _document(request, pk):
    qs = Document.objects.for_user(request.user).select_related(
        "company", "document_type", "responsible", "previous")
    return get_object_or_404(qs, pk=pk)


def _task(request, pk):
    qs = RenewalTask.objects.for_user(request.user).select_related("document__document_type", "document__company")
    return get_object_or_404(qs, pk=pk)


def _back(doc):
    return reverse("web:compliance_detail", args=[doc.pk])


# ------------------------------------------------------------------ dashboard and lists

@hr_perm("compliance.view_document")
def dashboard(request):
    return render(request, "web/compliance/dashboard.html", services.dashboard(request.user))


@hr_perm("compliance.view_document")
def attention_badge(request):
    n = services.attention_count(request.user)
    return HttpResponse(f'<span class="badge text-bg-danger rounded-pill">{n}</span>' if n else "")


@hr_perm("compliance.view_document")
def document_list(request):
    qs = Document.objects.for_user(request.user).select_related("company", "document_type", "responsible")
    history = request.GET.get("history") == "1"
    if not history:
        qs = qs.filter(is_current=True)
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(number__icontains=q) | Q(reference_name__icontains=q) | Q(agent_name__icontains=q)
                       | Q(document_type__name__icontains=q))
    company, dtype, state = (request.GET.get(k, "") for k in ("company", "type", "state"))
    if company.isdigit():
        qs = qs.filter(company_id=int(company))
    if dtype.isdigit():
        qs = qs.filter(document_type_id=int(dtype))
    docs = list(qs)
    if state in (schedule.EXPIRED, schedule.DUE, schedule.VALID):
        docs = [d for d in docs if d.is_current and d.state == state]
    companies = companies_for(request.user)
    return render(request, "web/compliance/documents.html", {
        "docs": docs, "q": q, "company": company, "dtype": dtype, "state": state, "history": history,
        "companies": companies, "multi_company": companies.count() > 1,
        "types": DocumentType.objects.filter(is_active=True, applies_to=Company_types),
        "states": [(schedule.EXPIRED, "Expired"), (schedule.DUE, "Due soon"), (schedule.VALID, "Valid")]})


# ------------------------------------------------------------------ documents

@hr_perm("compliance.view_document")
def document_detail(request, pk):
    doc = _document(request, pk)
    chain = [doc] + doc.older_versions()
    tasks = list(RenewalTask.objects.filter(document__in=chain)
                 .select_related("assignee", "document").prefetch_related("payments__paid_by"))
    return render(request, "web/compliance/detail.html", {
        "doc": doc, "history": chain[1:], "newer": doc.newer_version(), "tasks": tasks,
        "open_task": next((t for t in tasks if t.status == RenewalTask.Status.OPEN and t.document_id == doc.pk), None),
        "alerts": doc.alerts.all()[:8]})


@hr_perm("compliance.add_document")
def document_create(request):
    initial = {k: v for k, v in (("company", request.GET.get("company")),
                                 ("document_type", request.GET.get("type"))) if v and v.isdigit()}
    form = DocumentForm(request.POST or None, request.FILES or None, user=request.user, initial=initial)
    if request.method == "POST" and form.is_valid():
        try:
            doc = services.create_document(request.user, form.save(commit=False))
        except services.ComplianceError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, f"{doc.label} was added.")
            return redirect("web:compliance_detail", pk=doc.pk)
    return _form_page(request, form, "Add compliance document", reverse("web:compliance_documents"), "document")


@hr_perm("compliance.change_document")
def document_edit(request, pk):
    doc = _document(request, pk)
    if not doc.is_current:
        messages.error(request, "Older versions are kept as a record and cannot be edited.")
        return redirect("web:compliance_detail", pk=doc.pk)
    form = DocumentForm(request.POST or None, request.FILES or None, instance=doc, user=request.user)
    if request.method == "POST" and form.is_valid():
        services.update_document(form.save(commit=False), expiry_changed="expiry_date" in form.changed_data)
        messages.success(request, "Document updated.")
        return redirect("web:compliance_detail", pk=doc.pk)
    return _form_page(request, form, f"Edit: {doc.label}", _back(doc), "document")


@hr_perm("compliance.change_document")
def document_renew(request, pk):
    doc = _document(request, pk)
    if not doc.is_current:
        messages.error(request, "This document has already been renewed.")
        return redirect("web:compliance_detail", pk=doc.pk)
    form = RenewForm(request.POST or None, request.FILES or None, document=doc)
    if request.method == "POST" and form.is_valid():
        cd = form.cleaned_data
        try:
            new = services.renew_document(doc, request.user, expiry_date=cd["expiry_date"], number=cd["number"],
                                          issue_date=cd["issue_date"], file=cd["file"] or None, notes=cd["notes"])
        except services.ComplianceError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, f"{new.label} renewed until {new.expiry_date:%d %b %Y}.")
            return redirect("web:compliance_detail", pk=new.pk)
    return _form_page(request, form, f"Renew: {doc.label}", _back(doc), "renew",
                      intro=f"Current expiry: {doc.expiry_date:%d %b %Y}. Saving creates a new version and keeps this one as history.")


@hr_perm("compliance.view_document")
def document_file(request, pk):
    doc = _document(request, pk)
    return _serve(request, doc.file, doc, doc.company_id,
                  f"{slugify(doc.label)}-{doc.company.code}-{doc.expiry_date:%Y-%m-%d}".lower())


# ------------------------------------------------------------------ renewal tasks and payments

@hr_perm("compliance.add_renewaltask")
def task_create(request, pk):
    doc = _document(request, pk)
    form = RenewalTaskForm(request.POST or None, user=request.user, document=doc)
    if request.method == "POST" and form.is_valid():
        cd = form.cleaned_data
        try:
            services.open_renewal(doc, assignee=cd["assignee"], due_date=cd["due_date"],
                                  estimated_cost=cd["estimated_cost"], notes=cd["notes"])
        except services.ComplianceError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "Renewal started.")
            return redirect("web:compliance_detail", pk=doc.pk)
    return _form_page(request, form, f"Start renewal: {doc.label}", _back(doc), "task")


@hr_perm("compliance.change_renewaltask")
@require_POST
def task_cancel(request, pk):
    task = _task(request, pk)
    try:
        services.cancel_task(task)
    except services.ComplianceError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Renewal cancelled.")
    return redirect("web:compliance_detail", pk=task.document_id)


@hr_perm("compliance.add_renewalpayment")
def payment_create(request, pk):
    task = _task(request, pk)
    form = PaymentForm(request.POST or None, request.FILES or None, user=request.user,
                       instance=RenewalPayment(task=task))
    if request.method == "POST" and form.is_valid():
        try:
            services.record_payment(task, form.save(commit=False))
        except services.ComplianceError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "Payment recorded.")
            return redirect("web:compliance_detail", pk=task.document_id)
    return _form_page(request, form, f"Record payment: {task.document.label}", _back(task.document), "payment")


@hr_perm("compliance.view_renewalpayment")
def payment_receipt(request, pk):
    payment = get_object_or_404(RenewalPayment.objects.for_user(request.user)
                                .select_related("task__document__document_type"), pk=pk)
    doc = payment.task.document
    return _serve(request, payment.receipt, payment, payment.company_id,
                  f"receipt-{slugify(doc.label)}-{payment.paid_on:%Y-%m-%d}".lower())


def _serve(request, fieldfile, obj, company_id, base_name):
    """Send a private file. Every download is checked, and written to the audit log."""
    if not fieldfile:
        raise Http404
    try:
        handle = fieldfile.open("rb")
    except FileNotFoundError:
        raise Http404
    audit.log("download", obj, module="compliance", company_id=company_id)
    return FileResponse(handle, as_attachment=request.GET.get("download") == "1",
                        filename=base_name + Path(fieldfile.name).suffix.lower())


# ------------------------------------------------------------------ costs

@hr_perm("compliance.view_renewalpayment")
def costs(request):
    years = services.payment_years(request.user)
    try:
        year = int(request.GET.get("year", ""))
    except ValueError:
        year = years[0] if years else timezone.localdate().year
    return render(request, "web/compliance/costs.html", {
        "year": year, "years": years or [year], **services.cost_report(request.user, year)})


# ------------------------------------------------------------------ document types

@hr_perm("compliance.view_documenttype")
def type_list(request):
    return render(request, "web/compliance/types.html", {
        "types": DocumentType.objects.filter(applies_to=Company_types).order_by("-is_active", "name")})


@hr_perm("compliance.add_documenttype")
def type_create(request):
    form = DocumentTypeForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Document type added.")
        return redirect("web:compliance_types")
    return _form_page(request, form, "Add document type", reverse("web:compliance_types"), "type")


@hr_perm("compliance.change_documenttype")
def type_edit(request, pk):
    dtype = get_object_or_404(DocumentType, pk=pk, applies_to=Company_types)
    form = DocumentTypeForm(request.POST or None, instance=dtype)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Document type updated. The new alert days apply from the next daily check.")
        return redirect("web:compliance_types")
    return _form_page(request, form, f"Edit document type: {dtype.name}", reverse("web:compliance_types"), "type")


@hr_perm("compliance.change_documenttype")
@require_POST
def type_toggle(request, pk):
    dtype = get_object_or_404(DocumentType, pk=pk, applies_to=Company_types)
    dtype.is_active = not dtype.is_active
    dtype.save(update_fields=["is_active"])
    messages.success(request, f"{dtype.name} was {'reactivated' if dtype.is_active else 'deactivated'}.")
    return redirect("web:compliance_types")
