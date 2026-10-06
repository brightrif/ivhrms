from django.contrib import messages
from django.db.models import Prefetch, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from apps.compliance import schedule
from apps.compliance import services as compliance_services
from apps.compliance.models import Document, DocumentType
from apps.organization.services import companies_for
from apps.vehicles import services
from apps.vehicles.models import Vehicle

from apps.web.access import hr_perm
from apps.web.forms.vehicles import SellForm, VehicleDocumentForm, VehicleForm

# Full-page layouts, two columns on a laptop screen. See form_layout.html.
LAYOUTS = {
    "vehicle": [
        {"title": "Vehicle", "width": "col-lg-7",
         "rows": [["company", "plate_number"], ["kind", "make", "model"], ["year", "colour", "chassis_number"]]},
        {"title": "Use", "width": "col-lg-5",
         "rows": [["fuel_type", "ownership"], ["odometer"], ["notes"]]},
    ],
    "vehicle_document": [
        {"title": "Document", "width": "col-lg-7",
         "rows": [["document_type", "number"], ["issue_date", "expiry_date"]]},
        {"title": "Handling", "width": "col-lg-5",
         "rows": [["responsible", "agent_name"], ["file"], ["notes"]]},
    ],
    "sell": [{"title": "Sale", "width": "col-lg-5", "rows": [["sold_on"]]}],
}


def _form_page(request, form, title, back_url, layout, intro=""):
    return render(request, "web/form_layout.html", {
        "form": form, "title": title, "back_url": back_url, "layout": LAYOUTS[layout], "intro": intro})


def _vehicle(request, pk):
    return get_object_or_404(Vehicle.objects.for_user(request.user).select_related("company"), pk=pk)


def _detail_url(vehicle):
    return reverse("web:vehicle_detail", args=[vehicle.pk])


@hr_perm("vehicles.view_vehicle")
def vehicle_list(request):
    q = request.GET.get("q", "").strip()
    company = request.GET.get("company", "")
    status = request.GET.get("status", "")
    companies = companies_for(request.user)
    qs = (Vehicle.objects.for_user(request.user).select_related("company")
          .prefetch_related(Prefetch("documents", to_attr="current_docs",
                                     queryset=Document.objects.filter(is_current=True)
                                     .select_related("document_type"))))
    if q:
        qs = qs.filter(Q(plate_number__icontains=q) | Q(make__icontains=q) | Q(model__icontains=q)
                       | Q(chassis_number__icontains=q))
    if company.isdigit():
        qs = qs.filter(company_id=company)
    if status in Vehicle.Status.values:
        qs = qs.filter(status=status)
    else:
        qs = qs.exclude(status=Vehicle.Status.SOLD)          # sold vehicles show only when asked for
    vehicles = list(qs)
    for v in vehicles:
        states = [d.state for d in v.current_docs]
        v.expired, v.due = states.count(schedule.EXPIRED), states.count(schedule.DUE)
    return render(request, "web/vehicles/list.html", {
        "vehicles": vehicles, "q": q, "company": company, "status": status, "companies": companies,
        "multi_company": companies.count() > 1, "statuses": Vehicle.Status.choices})


@hr_perm("vehicles.view_vehicle")
def vehicle_detail(request, pk):
    vehicle = _vehicle(request, pk)
    docs = list(vehicle.documents.filter(is_current=True).select_related("company", "document_type", "responsible"))
    held = {d.document_type_id for d in docs}
    missing = [t for t in DocumentType.objects.filter(is_active=True, applies_to=DocumentType.AppliesTo.VEHICLE)
               if t.pk not in held]
    return render(request, "web/vehicles/detail.html", {"vehicle": vehicle, "docs": docs, "missing": missing})


@hr_perm("vehicles.add_vehicle")
def vehicle_create(request):
    form = VehicleForm(request.POST or None, user=request.user)
    if request.method == "POST" and form.is_valid():
        try:
            vehicle = services.create_vehicle(request.user, form.save(commit=False))
        except services.VehicleError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, f"{vehicle.plate_number} was added. You can add its documents now.")
            return redirect("web:vehicle_detail", pk=vehicle.pk)
    return _form_page(request, form, "Add vehicle", reverse("web:vehicle_list"), "vehicle")


@hr_perm("vehicles.change_vehicle")
def vehicle_edit(request, pk):
    vehicle = _vehicle(request, pk)
    if vehicle.status == Vehicle.Status.SOLD:
        messages.error(request, "Sold vehicles are kept as a record and cannot be edited.")
        return redirect("web:vehicle_detail", pk=vehicle.pk)
    form = VehicleForm(request.POST or None, instance=vehicle, user=request.user)
    if request.method == "POST" and form.is_valid():
        try:
            services.update_vehicle(form.save(commit=False))
        except services.VehicleError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "Vehicle updated.")
            return redirect("web:vehicle_detail", pk=vehicle.pk)
    return _form_page(request, form, f"Edit: {vehicle.plate_number}", _detail_url(vehicle), "vehicle")


@hr_perm("vehicles.change_vehicle")
def vehicle_sell(request, pk):
    vehicle = _vehicle(request, pk)
    if vehicle.status == Vehicle.Status.SOLD:
        messages.error(request, "This vehicle is already marked as sold.")
        return redirect("web:vehicle_detail", pk=vehicle.pk)
    form = SellForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            services.mark_sold(vehicle, form.cleaned_data["sold_on"])
        except services.VehicleError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, f"{vehicle.plate_number} was marked as sold.")
            return redirect("web:vehicle_detail", pk=vehicle.pk)
    return _form_page(request, form, f"Mark as sold: {vehicle.plate_number}", _detail_url(vehicle), "sell",
                      intro="The vehicle and its documents are kept as a record. Its documents stop sending alerts.")


@hr_perm("compliance.add_document")
def vehicle_document_create(request, pk):
    vehicle = _vehicle(request, pk)
    if vehicle.status == Vehicle.Status.SOLD:
        messages.error(request, "Documents cannot be added to a sold vehicle.")
        return redirect("web:vehicle_detail", pk=vehicle.pk)
    raw = request.GET.get("type", "")
    initial = {"document_type": raw} if raw.isdigit() else {}
    form = VehicleDocumentForm(request.POST or None, request.FILES or None, user=request.user,
                               vehicle=vehicle, initial=initial)
    if request.method == "POST" and form.is_valid():
        try:
            doc = compliance_services.create_document(request.user, form.save(commit=False))
        except compliance_services.ComplianceError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, f"{doc.label} was added.")
            return redirect("web:compliance_detail", pk=doc.pk)
    return _form_page(request, form, f"Add document: {vehicle.plate_number}", _detail_url(vehicle),
                      "vehicle_document")


@hr_perm("compliance.change_document")
def vehicle_document_edit(request, pk):
    qs = Document.objects.for_user(request.user).select_related("company", "document_type", "vehicle", "responsible")
    doc = get_object_or_404(qs, pk=pk, vehicle__isnull=False)
    if not doc.is_current:
        messages.error(request, "Older versions are kept as a record and cannot be edited.")
        return redirect("web:compliance_detail", pk=doc.pk)
    form = VehicleDocumentForm(request.POST or None, request.FILES or None, instance=doc, user=request.user,
                               vehicle=doc.vehicle)
    if request.method == "POST" and form.is_valid():
        compliance_services.update_document(form.save(commit=False),
                                            expiry_changed="expiry_date" in form.changed_data)
        messages.success(request, "Document updated.")
        return redirect("web:compliance_detail", pk=doc.pk)
    return _form_page(request, form, f"Edit: {doc.label}", reverse("web:compliance_detail", args=[doc.pk]),
                      "vehicle_document")
