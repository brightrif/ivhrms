from decimal import Decimal

from django.contrib import messages
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.vehicles import accidents, fines
from apps.vehicles.incidents import Accident, Fine
from apps.vehicles.services import VehicleError
from apps.vehicles.upkeep import ServiceRecord

from apps.web.access import hr_perm
from apps.web.forms.vehicle_incidents import AccidentForm, CloseAccidentForm, FineForm, LinkRepairForm, PayFineForm
from apps.web.forms.vehicle_upkeep import VoidForm
from apps.web.views.vehicles import _vehicle

LAYOUTS = {
    "fine": [
        {"title": "The fine", "width": "col-lg-7",
         "rows": [["fined_on", "reference"], ["offence"], ["location", "amount"]]},
        {"title": "Who pays", "width": "col-lg-5", "rows": [["driver"], ["charged_to_employee"], ["notes"]]},
    ],
    "pay": [{"title": "Payment", "width": "col-lg-6", "rows": [["paid_on", "amount"], ["reference"]]}],
    "accident": [
        {"title": "What happened", "width": "col-lg-7",
         "rows": [["occurred_on", "location"], ["description"], ["driver", "police_report_no"], ["fault"],
                  ["injuries", "third_party"], ["third_party_details"]]},
        {"title": "Insurance and notes", "width": "col-lg-5",
         "rows": [["claim_status", "claim_no"], ["insurance_recovered"], ["notes"]]},
    ],
    "close": [{"title": "Close accident", "width": "col-lg-5", "rows": [["closed_on"]]}],
    "link": [{"title": "Link a repair", "width": "col-lg-7", "rows": [["repair"]]}],
    "void": [{"title": "Cancel", "width": "col-lg-6", "rows": [["reason"]]}],
}


def _form_page(request, form, title, back_url, layout, intro=""):
    return render(request, "web/form_layout.html", {
        "form": form, "title": title, "back_url": back_url, "layout": LAYOUTS[layout], "intro": intro})


def _incidents_url(vehicle):
    return reverse("web:vehicle_incidents", args=[vehicle.pk])


def _fine(request, pk):
    return get_object_or_404(Fine.objects.for_user(request.user).select_related("vehicle", "driver"), pk=pk)


def _accident(request, pk):
    qs = Accident.objects.for_user(request.user).select_related("vehicle", "driver").prefetch_related("repairs")
    return get_object_or_404(qs, pk=pk)


def _total(rows):
    return sum(rows, Decimal("0"))


# ------------------------------------------------------------------ one vehicle

@hr_perm("vehicles.view_fine")
def vehicle_incidents(request, pk):
    vehicle = _vehicle(request, pk)
    all_fines = list(vehicle.fines.select_related("driver"))
    return render(request, "web/vehicles/incidents.html", {
        "vehicle": vehicle, "fines": all_fines,
        "unpaid_total": _total(f.amount for f in all_fines if not f.is_voided and f.status == Fine.Status.UNPAID),
        "accidents": vehicle.accidents.select_related("driver").prefetch_related("repairs")})


# ------------------------------------------------------------------ fines

@hr_perm("vehicles.view_fine")
def vehicle_fines(request):
    """Every fine on the vehicles the user can see. Unpaid ones by default: that is what needs action."""
    status = request.GET.get("status", "unpaid")
    q = request.GET.get("q", "").strip()
    qs = Fine.objects.for_user(request.user).select_related("vehicle", "driver").filter(is_voided=False)
    if status in (Fine.Status.UNPAID, Fine.Status.PAID):
        qs = qs.filter(status=status)
    else:
        status = "all"
    if q:
        qs = qs.filter(Q(vehicle__plate_number__icontains=q) | Q(reference__icontains=q) | Q(offence__icontains=q)
                       | Q(driver__first_name__icontains=q) | Q(driver__last_name__icontains=q))
    rows = list(qs[:300])
    return render(request, "web/vehicles/fines.html", {
        "fines": rows, "status": status, "q": q, "total": _total(f.display_amount for f in rows)})


@hr_perm("vehicles.add_fine")
def vehicle_fine_add(request, pk):
    vehicle = _vehicle(request, pk)
    form = FineForm(request.POST or None, user=request.user, vehicle=vehicle)
    if request.method == "POST" and form.is_valid():
        try:
            fine = fines.save_fine(form.save(commit=False))
        except VehicleError as exc:
            form.add_error(None, str(exc))
        else:
            who = f" Recorded against {fine.driver.full_name}." if fine.driver else ""
            messages.success(request, f"Fine added.{who}")
            return redirect(_incidents_url(vehicle))
    return _form_page(request, form, f"Add fine: {vehicle.plate_number}", _incidents_url(vehicle), "fine")


@hr_perm("vehicles.change_fine")
def vehicle_fine_edit(request, pk):
    fine = _fine(request, pk)
    if fine.is_voided or fine.status == Fine.Status.PAID:
        messages.error(request, "Only an unpaid fine can be edited. Cancel it and enter it again if it is wrong.")
        return redirect(_incidents_url(fine.vehicle))
    form = FineForm(request.POST or None, instance=fine, user=request.user, vehicle=fine.vehicle)
    if request.method == "POST" and form.is_valid():
        try:
            fines.save_fine(form.save(commit=False))
        except VehicleError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "Fine updated.")
            return redirect(_incidents_url(fine.vehicle))
    return _form_page(request, form, f"Edit fine: {fine.vehicle.plate_number}", _incidents_url(fine.vehicle), "fine")


@hr_perm("vehicles.pay_fine")
def vehicle_fine_pay(request, pk):
    fine = _fine(request, pk)
    if fine.is_voided or fine.status == Fine.Status.PAID:
        messages.error(request, "This fine is not waiting for payment.")
        return redirect(_incidents_url(fine.vehicle))
    form = PayFineForm(request.POST or None, fine=fine)
    if request.method == "POST" and form.is_valid():
        cd = form.cleaned_data
        try:
            fines.pay_fine(fine, cd["paid_on"], cd["amount"], cd["reference"])
        except VehicleError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "Payment recorded.")
            return redirect(_incidents_url(fine.vehicle))
    return _form_page(request, form, f"Pay fine: {fine.vehicle.plate_number}", _incidents_url(fine.vehicle), "pay",
                      intro=f"{fine.offence}, {fine.fined_on:%d %b %Y}. Amount due {fine.amount:.3f} BHD.")


@hr_perm("vehicles.change_fine")
def vehicle_fine_void(request, pk):
    fine = _fine(request, pk)
    if fine.is_voided:
        messages.error(request, "This fine is already cancelled.")
        return redirect(_incidents_url(fine.vehicle))
    form = VoidForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            fines.void_fine(fine, form.cleaned_data["reason"])
        except VehicleError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "The fine was cancelled.")
            return redirect(_incidents_url(fine.vehicle))
    return _form_page(request, form, f"Cancel fine: {fine.vehicle.plate_number}", _incidents_url(fine.vehicle), "void",
                      intro=f"{fine.offence}, {fine.fined_on:%d %b %Y}. Use this for a fine entered by mistake, "
                            "dismissed or waived. It stays in the history marked as cancelled.")


# ------------------------------------------------------------------ accidents

@hr_perm("vehicles.view_accident")
def vehicle_accident(request, pk):
    accident = _accident(request, pk)
    return render(request, "web/vehicles/accident.html", {
        "accident": accident, "vehicle": accident.vehicle, "repairs": accident.live_repairs})


@hr_perm("vehicles.add_accident")
def vehicle_accident_add(request, pk):
    vehicle = _vehicle(request, pk)
    form = AccidentForm(request.POST or None, user=request.user, vehicle=vehicle)
    if request.method == "POST" and form.is_valid():
        try:
            accident = accidents.save_accident(form.save(commit=False))
        except VehicleError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "Accident recorded. Link its repair here once it is logged in Maintenance.")
            return redirect("web:vehicle_accident", pk=accident.pk)
    return _form_page(request, form, f"Report accident: {vehicle.plate_number}", _incidents_url(vehicle), "accident")


@hr_perm("vehicles.change_accident")
def vehicle_accident_edit(request, pk):
    accident = _accident(request, pk)
    if accident.is_voided:
        messages.error(request, "A cancelled accident cannot be edited.")
        return redirect("web:vehicle_accident", pk=accident.pk)
    form = AccidentForm(request.POST or None, instance=accident, user=request.user, vehicle=accident.vehicle)
    if request.method == "POST" and form.is_valid():
        try:
            accidents.save_accident(form.save(commit=False))
        except VehicleError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "Accident updated.")
            return redirect("web:vehicle_accident", pk=accident.pk)
    return _form_page(request, form, f"Edit accident: {accident.vehicle.plate_number}",
                      reverse("web:vehicle_accident", args=[accident.pk]), "accident")


@hr_perm("vehicles.change_accident")
def vehicle_accident_close(request, pk):
    accident = _accident(request, pk)
    back = reverse("web:vehicle_accident", args=[accident.pk])
    form = CloseAccidentForm(request.POST or None, accident=accident)
    if request.method == "POST" and form.is_valid():
        try:
            accidents.close_accident(accident, form.cleaned_data["closed_on"])
        except VehicleError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "Accident closed.")
            return redirect(back)
    return _form_page(request, form, f"Close accident: {accident.vehicle.plate_number}", back, "close")


@hr_perm("vehicles.change_accident")
@require_POST
def vehicle_accident_reopen(request, pk):
    accident = _accident(request, pk)
    try:
        accidents.reopen_accident(accident)
    except VehicleError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Accident reopened.")
    return redirect("web:vehicle_accident", pk=accident.pk)


@hr_perm("vehicles.change_accident")
def vehicle_accident_void(request, pk):
    accident = _accident(request, pk)
    back = reverse("web:vehicle_accident", args=[accident.pk])
    form = VoidForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            accidents.void_accident(accident, form.cleaned_data["reason"])
        except VehicleError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "The accident was cancelled.")
            return redirect(back)
    return _form_page(request, form, f"Cancel accident: {accident.vehicle.plate_number}", back, "void",
                      intro="Use this for an accident entered by mistake. It stays in the history marked as cancelled.")


@hr_perm("vehicles.change_accident")
def vehicle_accident_link(request, pk):
    accident = _accident(request, pk)
    back = reverse("web:vehicle_accident", args=[accident.pk])
    form = LinkRepairForm(request.POST or None, accident=accident)
    if request.method == "POST" and form.is_valid():
        try:
            accidents.link_repair(accident, form.cleaned_data["repair"])
        except VehicleError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "Repair linked.")
            return redirect(back)
    return _form_page(request, form, f"Link a repair: {accident.vehicle.plate_number}", back, "link")


@hr_perm("vehicles.change_accident")
@require_POST
def vehicle_accident_unlink(request, pk, record_pk):
    accident = _accident(request, pk)
    record = get_object_or_404(ServiceRecord.objects.for_user(request.user), pk=record_pk)
    accidents.unlink_repair(accident, record)
    messages.success(request, "Repair unlinked.")
    return redirect("web:vehicle_accident", pk=accident.pk)
