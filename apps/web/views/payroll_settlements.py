from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.payroll import settlements
from apps.payroll.finalsettlement import FinalSettlement, SettlementItem
from apps.payroll.services import PayrollError

from apps.web.access import hr_perm
from apps.web.forms.payroll_settlements import ItemForm, PayForm, SettlementForm

LAYOUTS = {
    "case": [
        {"title": "Who and why", "width": "col-lg-5", "rows": [["employee"], ["reason"], ["last_working_day"]]},
        {"title": "Notice, leave and gratuity", "width": "col-lg-7",
         "rows": [["notice_date", "notice_required_days"], ["notice_treatment", "leave_type"],
                  ["gratuity_decision"], ["gratuity_note"]]}],
    "case_edit": [
        {"title": "Why", "width": "col-lg-5", "rows": [["reason"], ["last_working_day"]]},
        {"title": "Notice, leave and gratuity", "width": "col-lg-7",
         "rows": [["notice_date", "notice_required_days"], ["notice_treatment", "leave_type"],
                  ["gratuity_decision"], ["gratuity_note"]]}],
    "item": [{"title": "A line on the statement", "width": "col-lg-6", "rows": [["kind"], ["label"], ["amount"]]}],
    "pay": [{"title": "Payment", "width": "col-lg-5", "rows": [["paid_on"]]}],
}


def _form_page(request, form, title, back_url, layout, intro="", submit_label="Save"):
    return render(request, "web/form_layout.html", {
        "form": form, "title": title, "back_url": back_url, "layout": LAYOUTS[layout], "intro": intro,
        "submit_label": submit_label})


def _case(request, pk):
    return get_object_or_404(FinalSettlement.objects.for_user(request.user).select_related(
        "employee", "company", "leave_type", "prepared_by"), pk=pk)


def _url(case):
    return reverse("web:payroll_settlement", args=[case.pk])


def _act(request, target, function, success, *args):
    try:
        result = function(target, *args)
    except PayrollError as exc:
        messages.error(request, str(exc))
        return None
    messages.success(request, success(result) if callable(success) else success)
    return result


@hr_perm("payroll.view_finalsettlement")
def settlement_list(request):
    cases = FinalSettlement.objects.for_user(request.user).select_related("employee", "company")
    return render(request, "web/payroll/settlements.html", {"cases": cases})


@hr_perm("payroll.add_finalsettlement")
def settlement_create(request):
    form = SettlementForm(request.POST or None, user=request.user)
    if request.method == "POST" and form.is_valid():
        try:
            case = settlements.create_settlement(form.cleaned_data["employee"], user=request.user,
                                                 leave_type=form.cleaned_data["leave_type"], **form.values())
        except PayrollError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "The settlement was calculated. Read any red or amber messages, then send it for approval.")
            return redirect(_url(case))
    return _form_page(request, form, "New final settlement", reverse("web:payroll_settlements"), "case",
                      intro="Bahrain private-sector rules. Gratuity applies to non-Bahrainis only; the SIO pays the part "
                            "earned since 1 March 2024. Everything is worked out from the records and can be recalculated.")


@hr_perm("payroll.add_finalsettlement")
def settlement_edit(request, pk):
    case = _case(request, pk)
    if case.status != case.Status.DRAFT:
        messages.error(request, "Only a draft settlement can be changed.")
        return redirect(_url(case))
    form = SettlementForm(request.POST or None, user=request.user, case=case)
    if request.method == "POST" and form.is_valid():
        try:
            settlements.update_settlement(case, leave_type=form.cleaned_data["leave_type"], **form.values())
        except PayrollError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "Saved and recalculated.")
            return redirect(_url(case))
    return _form_page(request, form, f"Settlement: {case.employee.full_name}", _url(case), "case_edit")


@hr_perm("payroll.view_finalsettlement")
def settlement_detail(request, pk):
    case = _case(request, pk)
    items = list(case.items.all())
    return render(request, "web/payroll/settlement.html", {
        "case": case, "items": items, "details": case.details,
        "candidates": len(settlements.charge_candidates(case, request.user))
        if case.status == case.Status.DRAFT and request.user.has_perm("payroll.add_finalsettlement") else 0})


@hr_perm("payroll.add_finalsettlement")
@require_POST
def settlement_recalculate(request, pk):
    case = _case(request, pk)
    _act(request, case, settlements.calculate_settlement, "Recalculated from the records as they are now.")
    return redirect(_url(case))


@hr_perm("payroll.add_finalsettlement")
def item_add(request, pk):
    case = _case(request, pk)
    if case.status != case.Status.DRAFT:
        messages.error(request, "Only a draft settlement can be changed.")
        return redirect(_url(case))
    form = ItemForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        cd = form.cleaned_data
        try:
            settlements.add_item(case, kind=cd["kind"], label=cd["label"], amount=cd["amount"])
        except PayrollError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "Added.")
            return redirect(_url(case))
    return _form_page(request, form, f"Add a line: {case.employee.full_name}", _url(case), "item",
                      intro="For example a loan balance, an agreed bonus, or an asset not returned. Renewal fees and vehicle "
                            "fines charged to the person are picked up with one button on the settlement.")


@hr_perm("payroll.add_finalsettlement")
@require_POST
def item_remove(request, pk, item_pk):
    case = _case(request, pk)
    item = get_object_or_404(SettlementItem, pk=item_pk, settlement=case)
    _act(request, item, settlements.remove_item, "Removed.")
    return redirect(_url(case))


@hr_perm("payroll.add_finalsettlement")
@require_POST
def settlement_charges(request, pk):
    case = _case(request, pk)
    _act(request, case, settlements.import_charges,
         lambda n: (f"{n} charge{'s' if n != 1 else ''} added as deductions." if n else "There is nothing to add."),
         request.user)
    return redirect(_url(case))


@hr_perm("payroll.submit_finalsettlement")
@require_POST
def settlement_submit(request, pk):
    case = _case(request, pk)
    _act(request, case, settlements.submit_settlement, "Sent to Management. They will see it in their approvals inbox.",
         request.user)
    return redirect(_url(case))


@hr_perm("payroll.submit_finalsettlement")
@require_POST
def settlement_withdraw(request, pk):
    case = _case(request, pk)
    _act(request, case, settlements.withdraw_settlement, "Taken back. You can change it and send it again.", request.user)
    return redirect(_url(case))


@hr_perm("payroll.add_finalsettlement")
@require_POST
def settlement_cancel(request, pk):
    case = _case(request, pk)
    if _act(request, case, settlements.cancel_settlement, "The settlement was cancelled."):
        return redirect("web:payroll_settlements")
    return redirect(_url(case))


@hr_perm("payroll.pay_finalsettlement")
def settlement_pay(request, pk):
    case = _case(request, pk)
    if case.status != case.Status.APPROVED:
        messages.error(request, "A payment is recorded once the settlement is approved.")
        return redirect(_url(case))
    form = PayForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            settlements.mark_paid(case, form.cleaned_data["paid_on"])
        except PayrollError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "Payment recorded.")
            return redirect(_url(case))
    return _form_page(request, form, f"Payment: {case.employee.full_name}", _url(case), "pay",
                      intro=f"Net amount {case.total:.3f} BHD.")


def _document(request, case, template):
    from apps.audit import services as audit

    audit.log("download", case, module="payroll", subject_employee_id=case.employee_id, company_id=case.company_id,
              changes={"document": template.split("/")[-1].replace(".html", "")})
    return render(request, template, {"case": case, "items": list(case.items.all()), "details": case.details})


@hr_perm("payroll.view_finalsettlement")
def settlement_statement(request, pk):
    return _document(request, _case(request, pk), "web/payroll/statement.html")


@hr_perm("payroll.view_finalsettlement")
def settlement_certificate(request, pk):
    case = _case(request, pk)
    if not case.frozen:
        messages.error(request, "The end-of-service certificate is issued once the settlement is approved.")
        return redirect(_url(case))
    return _document(request, case, "web/payroll/certificate.html")
