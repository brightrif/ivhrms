"""Family members of staff and their visas: list, add, edit, documents and attached papers (HR)."""
from datetime import timedelta
from pathlib import Path

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import OuterRef, Q, Subquery
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.cache import patch_vary_headers
from django.utils.text import slugify
from django.views.decorators.http import require_POST

from apps.audit import services as audit
from apps.compliance import services as compliance
from apps.compliance.models import Document
from apps.dependants import coverage, services
from apps.dependants.models import Dependant, DependantAttachment
from apps.organization.services import companies_for

from apps.web.access import hr_perm
from apps.web.forms.dependants import AttachmentForm, DependantForm, FamilyDocumentForm

SOON_DAYS = 90

_PERSON_ROWS = [["relationship"], ["first_name", "last_name"], ["name_ar", "gender"],
                ["date_of_birth", "nationality"]]
# Full-page layouts, side by side on a laptop screen. See form_layout.html.
LAYOUTS = {
    "dependant": [{"title": "Family member", "width": "col-lg-7", "rows": [["employee"]] + _PERSON_ROWS},
                  {"title": "Notes", "width": "col-lg-5", "rows": [["notes"]]}],
    "dependant_edit": [{"title": "Family member", "width": "col-lg-7", "rows": _PERSON_ROWS},
                       {"title": "Notes", "width": "col-lg-5", "rows": [["notes"]]}],
    "document": [{"title": "Document", "width": "col-lg-7",
                  "rows": [["document_type", "number"], ["issue_date", "expiry_date"]]},
                 {"title": "Handling", "width": "col-lg-5",
                  "rows": [["responsible", "agent_name"], ["file"], ["notes"]]}],
    "paper": [{"title": "Paper", "width": "col-lg-7", "rows": [["kind", "description"], ["file"]]}],
}


def _form_page(request, form, title, back_url, layout, intro="", submit_label="Save"):
    return render(request, "web/form_layout.html", {
        "form": form, "title": title, "back_url": back_url, "layout": LAYOUTS[layout], "intro": intro,
        "submit_label": submit_label})


def _dependant(request, pk):
    return get_object_or_404(Dependant.objects.for_user(request.user).select_related("employee", "company"), pk=pk)


def _detail(dep):
    return reverse("web:employee_family_detail", args=[dep.pk])


def _who(dep):
    return f"{dep.get_relationship_display()} of {dep.employee.full_name}"


# ------------------------------------------------------------------ list

@hr_perm("dependants.view_dependant")
def family_list(request):
    today = timezone.localdate()
    companies = companies_for(request.user)
    current = Document.objects.filter(dependant=OuterRef("pk"), is_current=True)
    qs = (Dependant.objects.for_user(request.user).select_related("employee", "company")
          .annotate(visa_expiry=Subquery(current.filter(document_type__code="family-visa").values("expiry_date")[:1]),
                    next_expiry=Subquery(current.order_by("expiry_date").values("expiry_date")[:1])))

    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(first_name__icontains=q) | Q(last_name__icontains=q) | Q(name_ar__icontains=q)
                       | Q(employee__first_name__icontains=q) | Q(employee__last_name__icontains=q)
                       | Q(employee__employee_no__icontains=q))
    company, relationship = request.GET.get("company", ""), request.GET.get("relationship", "")
    status = request.GET.get("status", "active")
    expiring = request.GET.get("expiring") == "1"
    if company.isdigit():
        qs = qs.filter(company_id=int(company))
    if relationship in Dependant.Relationship.values:
        qs = qs.filter(relationship=relationship)
    if status == "active":
        qs = qs.filter(is_active=True)
    elif status == "inactive":
        qs = qs.filter(is_active=False)
    if expiring:
        qs = qs.filter(is_active=True, next_expiry__lte=today + timedelta(days=SOON_DAYS))

    page = Paginator(qs, 25).get_page(request.GET.get("page"))
    rows = list(page)
    missing = coverage.missing_by_dependant([d for d in rows if d.is_active])
    for d in rows:
        d.missing = missing.get(d.pk, [])
        d.visa_days = (d.visa_expiry - today).days if d.visa_expiry else None

    params = request.GET.copy()
    params.pop("page", None)
    ctx = {"page": page, "extra": params.urlencode(), "q": q, "company": company, "relationship": relationship,
           "status": status, "expiring": expiring, "companies": companies, "multi_company": companies.count() > 1,
           "relationships": Dependant.Relationship.choices, "soon_days": SOON_DAYS}
    partial = request.headers.get("HX-Request") == "true" and not request.headers.get("HX-History-Restore-Request")
    resp = render(request, "web/family/_table.html" if partial else "web/family/list.html", ctx)
    patch_vary_headers(resp, ["HX-Request"])
    return resp


# ------------------------------------------------------------------ one family member

@hr_perm("dependants.add_dependant")
def family_add(request):
    initial = {}
    raw = request.GET.get("employee", "")
    if raw.isdigit():
        initial["employee"] = int(raw)
    form = DependantForm(request.POST or None, user=request.user, initial=initial)
    if request.method == "POST" and form.is_valid():
        data = dict(form.cleaned_data)
        employee = data.pop("employee")
        try:
            dep = services.add_dependant(request.user, employee, **data)
        except services.DependantError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, f"{dep.full_name} was added. Add their passport, CPR card and visa next.")
            return redirect(_detail(dep))
    return _form_page(request, form, "Add a family member", reverse("web:employee_family"), "dependant")


@hr_perm("dependants.view_dependant")
def family_detail(request, pk):
    dep = _dependant(request, pk)
    user = request.user
    docs = (Document.objects.for_user(user).filter(dependant=dep, is_current=True)
            .select_related("document_type", "responsible").order_by("expiry_date"))
    can_files = user.has_perm("dependants.view_dependantattachment")
    companies = companies_for(user)
    return render(request, "web/family/detail.html", {
        "dep": dep, "docs": docs, "soon_days": SOON_DAYS, "multi_company": companies.count() > 1,
        "missing": services.missing_documents(dep) if dep.is_active else [],
        "files": dep.attachments.select_related("uploaded_by") if can_files else [], "can_files": can_files,
        "can_add_file": user.has_perm("dependants.add_dependantattachment") and dep.is_active,
        "can_edit": user.has_perm("dependants.change_dependant"),
        "can_add_doc": user.has_perm("compliance.add_document") and dep.is_active,
        "can_renew": user.has_perm("compliance.change_document") and dep.is_active})


@hr_perm("dependants.change_dependant")
def family_edit(request, pk):
    dep = _dependant(request, pk)
    form = DependantForm(request.POST or None, instance=dep, user=request.user)
    if request.method == "POST" and form.is_valid():
        try:
            services.update_dependant(dep, **form.cleaned_data)
        except services.DependantError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "Family member updated.")
            return redirect(_detail(dep))
    return _form_page(request, form, f"Edit: {dep.full_name}", _detail(dep), "dependant_edit",
                      intro=_who(dep))


@hr_perm("dependants.change_dependant")
@require_POST
def family_deactivate(request, pk):
    dep = _dependant(request, pk)
    try:
        services.deactivate_dependant(dep)
    except services.DependantError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f"{dep.full_name} is no longer sponsored. Their documents are no longer chased.")
    return redirect(_detail(dep))


# ------------------------------------------------------------------ documents and papers

@hr_perm("compliance.add_document")
def family_document_add(request, pk):
    dep = _dependant(request, pk)
    if not dep.is_active:
        messages.error(request, "This family member is no longer sponsored.")
        return redirect(_detail(dep))
    initial = {}
    raw = request.GET.get("type", "")
    if raw.isdigit():
        initial["document_type"] = int(raw)
    form = FamilyDocumentForm(request.POST or None, request.FILES or None, user=request.user, dependant=dep,
                              initial=initial)
    if request.method == "POST" and form.is_valid():
        try:
            doc = compliance.create_document(request.user, form.save(commit=False))
        except compliance.ComplianceError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, f"{doc.document_type.name} added for {dep.full_name}.")
            return redirect(_detail(dep))
    return _form_page(request, form, f"Add a document: {dep.full_name}", _detail(dep), "document",
                      intro=_who(dep))


@hr_perm("dependants.add_dependantattachment")
def family_paper_add(request, pk):
    dep = _dependant(request, pk)
    if not dep.is_active:
        messages.error(request, "This family member is no longer sponsored.")
        return redirect(_detail(dep))
    form = AttachmentForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        cd = form.cleaned_data
        try:
            services.add_attachment(request.user, dep, cd["kind"], cd["file"], cd["description"])
        except services.DependantError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "Paper attached.")
            return redirect(_detail(dep))
    return _form_page(request, form, f"Attach a paper: {dep.full_name}", _detail(dep), "paper",
                      intro="Marriage and birth certificates, medical reports. Kept private; every download is "
                            "recorded in the audit log.")


@hr_perm("dependants.view_dependantattachment")
def family_paper(request, pk):
    """Open (or, with ?download=1, download) an attached paper. Checked, and written to the audit log."""
    att = get_object_or_404(DependantAttachment.objects.for_user(request.user).select_related("dependant"), pk=pk)
    try:
        handle = att.file.open("rb")
    except FileNotFoundError:
        raise Http404
    audit.log("download", att, module="dependants", company_id=att.company_id)
    name = slugify(f"{att.dependant.full_name}-{att.get_kind_display()}") or "paper"
    response = FileResponse(handle, as_attachment=request.GET.get("download") == "1",
                            filename=name + Path(att.file.name).suffix.lower())
    response["X-Content-Type-Options"] = "nosniff"
    return response
