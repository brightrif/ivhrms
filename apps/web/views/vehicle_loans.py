from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from apps.vehicles import financing
from apps.vehicles.loans import LoanInstallment, VehicleLoan
from apps.vehicles.services import VehicleError

from apps.web.access import hr_perm
from apps.web.forms.vehicle_loans import LoanForm, PayInstallmentForm, SettleForm
from apps.web.forms.vehicle_upkeep import VoidForm
from apps.web.views.vehicles import _vehicle

LAYOUTS = {
    "loan": [
        {"title": "The loan", "width": "col-lg-6",
         "rows": [["lender", "account_no"], ["financed_amount", "down_payment"], ["notes"]]},
        {"title": "Repayment", "width": "col-lg-6",
         "rows": [["installment_count", "installment_amount"], ["final_installment_amount", "first_due_on"],
                  ["already_paid"]]},
    ],
    "pay": [{"title": "Payment", "width": "col-lg-6", "rows": [["paid_on", "amount"], ["reference"]]}],
    "settle": [{"title": "Early settlement", "width": "col-lg-6", "rows": [["settled_on", "amount"], ["reference"]]}],
    "void": [{"title": "Cancel", "width": "col-lg-6", "rows": [["reason"]]}],
}


def _form_page(request, form, title, back_url, layout, intro=""):
    return render(request, "web/form_layout.html", {
        "form": form, "title": title, "back_url": back_url, "layout": LAYOUTS[layout], "intro": intro})


def _loan_url(vehicle):
    return reverse("web:vehicle_loan", args=[vehicle.pk])


def _loan(request, pk):
    return get_object_or_404(VehicleLoan.objects.for_user(request.user).select_related("vehicle"), pk=pk)


def _installment(request, pk):
    qs = LoanInstallment.objects.for_user(request.user).select_related("loan", "loan__vehicle")
    return get_object_or_404(qs, pk=pk)


@hr_perm("vehicles.view_vehicleloan")
def vehicle_loans(request):
    return render(request, "web/vehicles/loans.html", financing.overview(request.user))


@hr_perm("vehicles.view_vehicleloan")
def vehicle_loan(request, pk):
    vehicle = _vehicle(request, pk)
    loans = list(vehicle.loans.filter(is_voided=False).order_by("-first_due_on", "-id"))
    chosen = request.GET.get("loan", "")
    loan = next((l for l in loans if str(l.pk) == chosen), None) or financing.current_loan(vehicle)
    return render(request, "web/vehicles/loan.html", {
        "vehicle": vehicle, "loan": loan, "loans": loans, "today": timezone.localdate(),
        "summary": financing.loan_summary(loan) if loan else None,
        "has_active": any(l.status == VehicleLoan.Status.ACTIVE for l in loans)})


@hr_perm("vehicles.add_vehicleloan")
def vehicle_loan_add(request, pk):
    vehicle = _vehicle(request, pk)
    form = LoanForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        cd = form.cleaned_data
        try:
            financing.create_loan(
                vehicle, lender=cd["lender"], account_no=cd["account_no"], financed_amount=cd["financed_amount"],
                down_payment=cd["down_payment"], installment_count=cd["installment_count"],
                installment_amount=cd["installment_amount"], final_installment_amount=cd["final_installment_amount"],
                first_due_on=cd["first_due_on"], already_paid=cd["already_paid"], notes=cd["notes"])
        except VehicleError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "Loan recorded and its installment schedule built.")
            return redirect(_loan_url(vehicle))
    return _form_page(request, form, f"Add loan: {vehicle.plate_number}", _loan_url(vehicle), "loan")


@hr_perm("vehicles.pay_loaninstallment")
def vehicle_installment_pay(request, pk):
    inst = _installment(request, pk)
    vehicle = inst.loan.vehicle
    if inst.status != LoanInstallment.Status.DUE or inst.loan.status != VehicleLoan.Status.ACTIVE:
        messages.error(request, "This installment is not waiting for payment.")
        return redirect(_loan_url(vehicle))
    form = PayInstallmentForm(request.POST or None, installment=inst)
    if request.method == "POST" and form.is_valid():
        cd = form.cleaned_data
        try:
            financing.pay_installment(inst, cd["paid_on"], cd["amount"], cd["reference"])
        except VehicleError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, f"Installment {inst.number} recorded as paid.")
            return redirect(_loan_url(vehicle))
    return _form_page(request, form, f"Pay installment {inst.number}: {vehicle.plate_number}", _loan_url(vehicle), "pay",
                      intro=f"Due {inst.due_on:%d %b %Y}, {inst.amount:.3f} BHD.")


@hr_perm("vehicles.change_vehicleloan")
def vehicle_installment_undo(request, pk):
    inst = _installment(request, pk)
    vehicle = inst.loan.vehicle
    form = VoidForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            financing.reverse_payment(inst, form.cleaned_data["reason"])
        except VehicleError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, f"The payment of installment {inst.number} was undone.")
            return redirect(_loan_url(vehicle))
    return _form_page(request, form, f"Undo payment {inst.number}: {vehicle.plate_number}", _loan_url(vehicle), "void",
                      intro="Use this when a payment was recorded against the wrong installment or by mistake. "
                            "The installment becomes due again.")


@hr_perm("vehicles.change_vehicleloan")
def vehicle_loan_settle(request, pk):
    loan = _loan(request, pk)
    vehicle = loan.vehicle
    if loan.status != VehicleLoan.Status.ACTIVE:
        messages.error(request, "Only an active loan can be settled.")
        return redirect(_loan_url(vehicle))
    outstanding = financing.loan_summary(loan).outstanding
    form = SettleForm(request.POST or None, outstanding=outstanding)
    if request.method == "POST" and form.is_valid():
        cd = form.cleaned_data
        try:
            financing.settle_early(loan, cd["settled_on"], cd["amount"], cd["reference"])
        except VehicleError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "The loan was settled. The vehicle can now be sold.")
            return redirect(_loan_url(vehicle))
    return _form_page(request, form, f"Settle early: {vehicle.plate_number}", _loan_url(vehicle), "settle",
                      intro="Every installment still due is cleared and the loan is closed.")


@hr_perm("vehicles.change_vehicleloan")
def vehicle_loan_void(request, pk):
    loan = _loan(request, pk)
    vehicle = loan.vehicle
    if loan.is_voided:
        messages.error(request, "This loan is already cancelled.")
        return redirect(_loan_url(vehicle))
    form = VoidForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            financing.void_loan(loan, form.cleaned_data["reason"])
        except VehicleError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "The loan was cancelled.")
            return redirect(_loan_url(vehicle))
    return _form_page(request, form, f"Cancel loan: {vehicle.plate_number}", _loan_url(vehicle), "void",
                      intro="Only for a loan entered by mistake, and only while no payment is recorded against it.")
