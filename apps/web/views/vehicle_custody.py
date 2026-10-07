from decimal import Decimal

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.vehicles import assignments, handovers, recoveries
from apps.vehicles.custody import CustodyRequest
from apps.vehicles.incidents import Fine
from apps.vehicles.models import Vehicle
from apps.vehicles.services import VehicleError

from apps.web.access import hr_perm
from apps.web.forms.vehicle_custody import CustodyForm, RecoverForm
from apps.web.forms.vehicle_usage import AssignForm
from apps.web.forms.vehicle_upkeep import VoidForm
from apps.web.views.vehicle_usage import _form_page as _assign_page
from apps.web.views.vehicles import _detail_url, _vehicle

LAYOUTS = {
    "custody": [
        {"title": "Decision", "width": "col-lg-7", "rows": [["decision"], ["new_driver", "planned_on"]]},
        {"title": "Why", "width": "col-lg-5", "rows": [["reason"]]},
    ],
    "recover": [{"title": "Recovery", "width": "col-lg-6", "rows": [["recovered_on"], ["note"]]}],
    "void": [{"title": "Undo", "width": "col-lg-6", "rows": [["reason"]]}],
}


def _form_page(request, form, title, back_url, layout, intro=""):
    return render(request, "web/form_layout.html", {
        "form": form, "title": title, "back_url": back_url, "layout": LAYOUTS[layout], "intro": intro})


# ------------------------------------------------------------------ workshop

@hr_perm("vehicles.change_vehicle")
@require_POST
def vehicle_workshop_send(request, pk):
    vehicle = _vehicle(request, pk)
    try:
        handovers.send_to_workshop(vehicle)
    except VehicleError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f"{vehicle.plate_number} is now in the workshop.")
    return redirect(_detail_url(vehicle))


@hr_perm("vehicles.change_vehicle")
@require_POST
def vehicle_workshop_back(request, pk):
    vehicle = _vehicle(request, pk)
    try:
        handovers.back_from_workshop(vehicle)
    except VehicleError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f"{vehicle.plate_number} is back from the workshop.")
    return redirect(_detail_url(vehicle))


# ------------------------------------------------------------------ one-step hand-over

@hr_perm("vehicles.add_vehicleassignment")
def vehicle_handover(request, pk):
    vehicle = _vehicle(request, pk)
    if vehicle.status == Vehicle.Status.SOLD:
        messages.error(request, "A sold vehicle cannot be handed over.")
        return redirect(_detail_url(vehicle))
    current = assignments.current_assignment(vehicle)
    initial = {"employee": request.GET["employee"]} if request.GET.get("employee", "").isdigit() else {}
    form = AssignForm(request.POST or None, user=request.user, vehicle=vehicle, initial=initial)
    if current:
        form.fields["employee"].queryset = form.fields["employee"].queryset.exclude(pk=current.employee_id)
    if request.method == "POST" and form.is_valid():
        cd = form.cleaned_data
        try:
            handovers.hand_over(vehicle, cd["employee"], cd["assigned_from"], cd["odometer"], notes=cd["notes"],
                                licence_override=cd["confirm_licence"])
        except assignments.LicenceProblem as exc:
            form.add_error(None, f"{exc} Tick “Assign anyway” if you have checked it elsewhere.")
        except VehicleError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, f"{vehicle.plate_number} was handed over to {cd['employee'].full_name}.")
            return redirect(_detail_url(vehicle))
    intro = (f"It is with {current.employee.full_name} now. It is taken back and given to the new driver on the "
             "same date and odometer reading." if current else "Nobody has it now, so this simply assigns it.")
    return _assign_page(request, form, f"Hand over: {vehicle.plate_number}", _detail_url(vehicle), "assign", intro=intro)


# ------------------------------------------------------------------ custody decision (approval workflow)

@hr_perm("vehicles.add_custodyrequest")
def vehicle_custody_request(request, pk):
    vehicle = _vehicle(request, pk)
    holding = assignments.current_assignment(vehicle)
    if holding is None:
        messages.error(request, "Nobody holds this vehicle, so there is nothing to decide.")
        return redirect(_detail_url(vehicle))
    holder = holding.employee
    form = CustodyForm(request.POST or None, user=request.user, vehicle=vehicle, holder=holder)
    if request.method == "POST" and form.is_valid():
        cd = form.cleaned_data
        try:
            handovers.request_custody_decision(holding, cd["decision"], requested_by=request.user,
                                               new_driver=cd["new_driver"], planned_on=cd["planned_on"],
                                               reason=cd["reason"])
        except VehicleError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "Sent for approval. Management will see it in their approvals inbox.")
            return redirect(_detail_url(vehicle))
    status = "has left" if holder.status == "separated" else "is on notice" if holder.status == "on_notice" else "is active"
    return _form_page(request, form, f"Decide: {vehicle.plate_number}", _detail_url(vehicle), "custody",
                      intro=f"{holder.full_name} {status} and holds this vehicle. The decision goes to Management for "
                            "approval; once approved, record the hand-over or return when it happens.")


@hr_perm("vehicles.add_custodyrequest")
@require_POST
def vehicle_custody_cancel(request, pk):
    custody = get_object_or_404(CustodyRequest.objects.for_user(request.user).select_related("vehicle"), pk=pk)
    try:
        handovers.cancel_request(custody, request.user)
    except VehicleError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "The request was cancelled.")
    return redirect(_detail_url(custody.vehicle))


# ------------------------------------------------------------------ fines to recover from drivers

def _fine(request, pk):
    return get_object_or_404(Fine.objects.for_user(request.user).select_related("vehicle", "driver"), pk=pk)


@hr_perm("vehicles.view_fine")
def vehicle_fines_recover(request):
    people = recoveries.to_recover(request.user)
    return render(request, "web/vehicles/recover.html", {
        "people": people, "total": sum((p.total for p in people), Decimal("0")),
        "recovered": recoveries.recovered(request.user)})


@hr_perm("vehicles.pay_fine")
def vehicle_fine_recover(request, pk):
    fine = _fine(request, pk)
    back = reverse("web:vehicle_fines_recover")
    form = RecoverForm(request.POST or None, fine=fine)
    if request.method == "POST" and form.is_valid():
        cd = form.cleaned_data
        try:
            recoveries.mark_recovered(fine, cd["recovered_on"], cd["note"])
        except VehicleError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "Marked as recovered.")
            return redirect(back)
    who = fine.driver.full_name if fine.driver else "the driver"
    return _form_page(request, form, f"Recovered from {who}", back, "recover",
                      intro=f"{fine.vehicle.plate_number}, {fine.offence}, {fine.fined_on:%d %b %Y}: "
                            f"{fine.display_amount:.3f} BHD.")


@hr_perm("vehicles.pay_fine")
def vehicle_fine_unrecover(request, pk):
    fine = _fine(request, pk)
    back = reverse("web:vehicle_fines_recover")
    form = VoidForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            recoveries.undo_recovery(fine, form.cleaned_data["reason"])
        except VehicleError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "The recovery was undone. The fine is to be recovered again.")
            return redirect(back)
    return _form_page(request, form, f"Undo recovery: {fine.vehicle.plate_number}", back, "void",
                      intro="Use this when a recovery was recorded by mistake.")
