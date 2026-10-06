from django.contrib import messages
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from apps.vehicles import fuel, maintenance
from apps.vehicles.models import Vehicle
from apps.vehicles.services import VehicleError
from apps.vehicles.upkeep import FuelFill, ServicePlan, ServiceRecord

from apps.web.access import hr_perm
from apps.web.forms.vehicle_upkeep import FuelForm, PlanForm, ServiceForm, VoidForm
from apps.web.views.vehicles import _vehicle

LAYOUTS = {
    "fuel": [
        {"title": "Fill-up", "width": "col-lg-7", "rows": [["filled_on", "odometer"], ["litres", "cost"], ["full_tank"]]},
        {"title": "Details", "width": "col-lg-5", "rows": [["station"], ["notes"]]},
    ],
    "void": [{"title": "Cancel entry", "width": "col-lg-6", "rows": [["reason"]]}],
    "plan": [
        {"title": "Plan", "width": "col-lg-7", "rows": [["name"], ["every_km", "every_months"], ["warn_km", "warn_days"]]},
        {"title": "Last done", "width": "col-lg-5", "rows": [["baseline_on", "baseline_km"], ["is_active"]]},
    ],
    "service": [
        {"title": "Work done", "width": "col-lg-7",
         "rows": [["serviced_on", "odometer"], ["kind", "garage"], ["description"]]},
        {"title": "Cost and plans", "width": "col-lg-5",
         "rows": [["parts_cost", "labour_cost"], ["invoice_no"], ["plans"]]},
    ],
}


def _form_page(request, form, title, back_url, layout, intro=""):
    return render(request, "web/form_layout.html", {
        "form": form, "title": title, "back_url": back_url, "layout": LAYOUTS[layout], "intro": intro})


def _fuel_url(vehicle):
    return reverse("web:vehicle_fuel", args=[vehicle.pk])


def _service_url(vehicle):
    return reverse("web:vehicle_service", args=[vehicle.pk])


# ------------------------------------------------------------------ fuel

@hr_perm("vehicles.view_fuelfill")
def vehicle_fuel(request, pk):
    vehicle = _vehicle(request, pk)
    return render(request, "web/vehicles/fuel.html", {"vehicle": vehicle, **fuel.fuel_stats(vehicle)})


@hr_perm("vehicles.add_fuelfill")
def vehicle_fuel_add(request, pk):
    vehicle = _vehicle(request, pk)
    if vehicle.status == Vehicle.Status.SOLD:
        messages.error(request, "A sold vehicle cannot take new fuel entries.")
        return redirect(_fuel_url(vehicle))
    form = FuelForm(request.POST or None, vehicle=vehicle)
    if request.method == "POST" and form.is_valid():
        cd = form.cleaned_data
        try:
            fuel.add_fill(vehicle, filled_on=cd["filled_on"], litres=cd["litres"], cost=cd["cost"],
                          km=cd["odometer"], full_tank=cd["full_tank"], station=cd["station"], notes=cd["notes"])
        except VehicleError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "Fuel entry added.")
            return redirect(_fuel_url(vehicle))
    return _form_page(request, form, f"Add fuel: {vehicle.plate_number}", _fuel_url(vehicle), "fuel")


@hr_perm("vehicles.change_fuelfill")
def vehicle_fuel_void(request, pk):
    fill = get_object_or_404(FuelFill.objects.for_user(request.user).select_related("vehicle"), pk=pk)
    vehicle = fill.vehicle
    if fill.is_voided:
        messages.error(request, "This entry is already cancelled.")
        return redirect(_fuel_url(vehicle))
    form = VoidForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            fuel.void_fill(fill, form.cleaned_data["reason"])
        except VehicleError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "The entry was cancelled.")
            return redirect(_fuel_url(vehicle))
    return _form_page(request, form, f"Cancel fuel entry: {vehicle.plate_number}", _fuel_url(vehicle), "void",
                      intro=f"{fill.litres} L on {fill.filled_on:%d %b %Y}. It stays in the history marked as "
                            "cancelled, and the odometer reading it added is cancelled with it.")


# ------------------------------------------------------------------ service

@hr_perm("vehicles.view_serviceplan")
def vehicle_service(request, pk):
    vehicle = _vehicle(request, pk)
    return render(request, "web/vehicles/service.html", {
        "vehicle": vehicle, "plans": maintenance.vehicle_plans(vehicle),
        "switched_off": vehicle.service_plans.filter(is_active=False),
        "records": vehicle.service_records.prefetch_related("plans")[:50]})


@hr_perm("vehicles.add_serviceplan")
def vehicle_plan_add(request, pk):
    vehicle = _vehicle(request, pk)
    if vehicle.status == Vehicle.Status.SOLD:
        messages.error(request, "A sold vehicle cannot take new plans.")
        return redirect(_service_url(vehicle))
    form = PlanForm(request.POST or None, vehicle=vehicle)
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                plan = form.save()
        except IntegrityError:
            form.add_error("name", "This vehicle already has an active plan with this name.")
        else:
            messages.success(request, f"Plan '{plan.name}' added.")
            return redirect(_service_url(vehicle))
    return _form_page(request, form, f"Add service plan: {vehicle.plate_number}", _service_url(vehicle), "plan")


@hr_perm("vehicles.change_serviceplan")
def vehicle_plan_edit(request, pk):
    plan = get_object_or_404(ServicePlan.objects.for_user(request.user).select_related("vehicle"), pk=pk)
    vehicle = plan.vehicle
    if vehicle.status == Vehicle.Status.SOLD:
        messages.error(request, "Plans of a sold vehicle cannot be changed.")
        return redirect(_service_url(vehicle))
    form = PlanForm(request.POST or None, instance=plan, vehicle=vehicle)
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                form.save()
        except IntegrityError:
            form.add_error("name", "This vehicle already has an active plan with this name.")
        else:
            messages.success(request, "Plan updated.")
            return redirect(_service_url(vehicle))
    return _form_page(request, form, f"Edit plan: {plan.name}", _service_url(vehicle), "plan")


@hr_perm("vehicles.add_servicerecord")
def vehicle_service_add(request, pk):
    vehicle = _vehicle(request, pk)
    if vehicle.status == Vehicle.Status.SOLD:
        messages.error(request, "A sold vehicle cannot take new service entries.")
        return redirect(_service_url(vehicle))
    form = ServiceForm(request.POST or None, vehicle=vehicle)
    if request.method == "POST" and form.is_valid():
        cd = form.cleaned_data
        try:
            maintenance.add_service(
                vehicle, serviced_on=cd["serviced_on"], km=cd["odometer"], kind=cd["kind"], plans=cd["plans"],
                garage=cd["garage"], description=cd["description"], parts_cost=cd["parts_cost"],
                labour_cost=cd["labour_cost"], invoice_no=cd["invoice_no"])
        except VehicleError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "Service entry added.")
            return redirect(_service_url(vehicle))
    return _form_page(request, form, f"Record service: {vehicle.plate_number}", _service_url(vehicle), "service")


@hr_perm("vehicles.change_servicerecord")
def vehicle_service_void(request, pk):
    record = get_object_or_404(ServiceRecord.objects.for_user(request.user).select_related("vehicle"), pk=pk)
    vehicle = record.vehicle
    if record.is_voided:
        messages.error(request, "This entry is already cancelled.")
        return redirect(_service_url(vehicle))
    form = VoidForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            maintenance.void_service(record, form.cleaned_data["reason"])
        except VehicleError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "The entry was cancelled.")
            return redirect(_service_url(vehicle))
    return _form_page(request, form, f"Cancel service entry: {vehicle.plate_number}", _service_url(vehicle), "void",
                      intro=f"{record.get_kind_display()} on {record.serviced_on:%d %b %Y}. It stays in the history "
                            "marked as cancelled, and any plan it completed goes back to its earlier counter.")


@hr_perm("vehicles.view_serviceplan")
def vehicle_service_due(request):
    return render(request, "web/vehicles/service_due.html", {"plans": maintenance.due_plans(request.user)})
