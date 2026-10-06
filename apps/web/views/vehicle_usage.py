from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render

from apps.vehicles import assignments, odometer
from apps.vehicles.models import Vehicle
from apps.vehicles.services import VehicleError
from apps.vehicles.usage import OdometerReading, VehicleAssignment

from apps.web.access import hr_perm
from apps.web.forms.vehicle_usage import AssignForm, OdometerForm, ReturnForm
from apps.web.views.vehicles import _detail_url, _vehicle

LAYOUTS = {
    "assign": [
        {"title": "Hand-over", "width": "col-lg-6",
         "rows": [["employee"], ["assigned_from", "odometer"]]},
        {"title": "Checks and notes", "width": "col-lg-6", "rows": [["confirm_licence"], ["notes"]]},
    ],
    "return": [
        {"title": "Return", "width": "col-lg-6", "rows": [["returned_on", "odometer"]]},
        {"title": "Notes", "width": "col-lg-6", "rows": [["notes"]]},
    ],
    "odometer": [
        {"title": "Reading", "width": "col-lg-6", "rows": [["reading_on", "odometer"], ["note"]]},
    ],
}


def _form_page(request, form, title, back_url, layout, intro=""):
    return render(request, "web/form_layout.html", {
        "form": form, "title": title, "back_url": back_url, "layout": LAYOUTS[layout], "intro": intro})


@hr_perm("vehicles.add_vehicleassignment")
def vehicle_assign(request, pk):
    vehicle = _vehicle(request, pk)
    if vehicle.status == Vehicle.Status.SOLD:
        messages.error(request, "A sold vehicle cannot be assigned.")
        return redirect("web:vehicle_detail", pk=vehicle.pk)
    holder = assignments.current_assignment(vehicle)
    if holder:
        messages.error(request, f"This vehicle is with {holder.employee.full_name}. Return it first.")
        return redirect("web:vehicle_detail", pk=vehicle.pk)
    form = AssignForm(request.POST or None, user=request.user, vehicle=vehicle)
    if request.method == "POST" and form.is_valid():
        cd = form.cleaned_data
        try:
            assignments.assign_vehicle(vehicle, cd["employee"], cd["assigned_from"], cd["odometer"],
                                       notes=cd["notes"], licence_override=cd["confirm_licence"])
        except assignments.LicenceProblem as exc:
            form.add_error(None, f"{exc} Tick “Assign anyway” if you have checked it elsewhere.")
        except VehicleError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, f"{vehicle.plate_number} was handed over to {cd['employee'].full_name}.")
            return redirect("web:vehicle_detail", pk=vehicle.pk)
    return _form_page(request, form, f"Assign: {vehicle.plate_number}", _detail_url(vehicle), "assign")


@hr_perm("vehicles.change_vehicleassignment")
def vehicle_return(request, pk):
    qs = VehicleAssignment.objects.for_user(request.user).select_related("vehicle", "employee")
    assignment = get_object_or_404(qs, pk=pk)
    vehicle = assignment.vehicle
    if not assignment.is_open:
        messages.error(request, "This vehicle has already been returned.")
        return redirect("web:vehicle_detail", pk=vehicle.pk)
    form = ReturnForm(request.POST or None, assignment=assignment)
    if request.method == "POST" and form.is_valid():
        cd = form.cleaned_data
        try:
            assignments.return_vehicle(assignment, cd["returned_on"], cd["odometer"], notes=cd["notes"])
        except VehicleError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, f"{vehicle.plate_number} was returned by {assignment.employee.full_name}.")
            return redirect("web:vehicle_detail", pk=vehicle.pk)
    return _form_page(request, form, f"Return: {vehicle.plate_number}", _detail_url(vehicle), "return",
                      intro=f"With {assignment.employee.full_name} since {assignment.assigned_from:%d %b %Y} "
                            f"(handed over at {assignment.start_odometer} km).")


@hr_perm("vehicles.add_odometerreading")
def vehicle_odometer(request, pk):
    vehicle = _vehicle(request, pk)
    if vehicle.status == Vehicle.Status.SOLD:
        messages.error(request, "A sold vehicle's odometer cannot be changed.")
        return redirect("web:vehicle_detail", pk=vehicle.pk)
    form = OdometerForm(request.POST or None, vehicle=vehicle)
    if request.method == "POST" and form.is_valid():
        cd = form.cleaned_data
        try:
            odometer.record_odometer(vehicle, cd["odometer"], cd["reading_on"],
                                     source=OdometerReading.Source.MANUAL, note=cd["note"])
        except VehicleError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "Odometer reading recorded.")
            return redirect("web:vehicle_detail", pk=vehicle.pk)
    return _form_page(request, form, f"Odometer: {vehicle.plate_number}", _detail_url(vehicle), "odometer")
