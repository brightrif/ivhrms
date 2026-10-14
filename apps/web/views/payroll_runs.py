from django.contrib import messages
from django.db.models import Count
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.text import slugify
from django.views.decorators.http import require_POST

from apps.organization.services import companies_for
from apps.payroll import labor_payroll, outputs, runs
from apps.payroll.payrun import PayrollAdjustment, PayrollLine, PayrollPolicy, PayrollRun
from apps.payroll.policy import set_policy
from apps.payroll.services import PayrollError

from apps.web.access import hr_perm
from apps.web.forms.payroll_runs import AdjustmentForm, PayForm, PolicyForm, RunForm

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
LAYOUTS = {
    "run": [{"title": "Month", "width": "col-lg-6", "rows": [["company"], ["kind"], ["month", "year"]]}],
    "adjustment": [{"title": "Adjustment", "width": "col-lg-7",
                    "rows": [["employee"], ["kind", "category"], ["label"], ["amount"]]}],
    "pay": [{"title": "Payment", "width": "col-lg-6", "rows": [["paid_on"], ["lines"]]}],
    "policy": [
        {"title": "From when", "width": "col-lg-4", "rows": [["company"], ["effective_from"], ["days_in_month"]]},
        {"title": "SIO", "width": "col-lg-8",
         "rows": [["sio_employee_rate", "sio_employer_rate"], ["sio_employer_rate_other"],
                  ["sio_wage_floor", "sio_wage_cap"], ["sio_on_paid_days"]]},
    ],
}


def _form_page(request, form, title, back_url, layout, intro="", submit_label="Save"):
    return render(request, "web/form_layout.html", {
        "form": form, "title": title, "back_url": back_url, "layout": LAYOUTS[layout], "intro": intro,
        "submit_label": submit_label})


def _run(request, pk):
    return get_object_or_404(PayrollRun.objects.for_user(request.user).select_related("company", "prepared_by"), pk=pk)


def _url(run):
    return reverse("web:payroll_run", args=[run.pk])


def _act(request, run, function, success, *args):
    """Run a payroll action from a button. A broken rule is shown as a message, never as a crash."""
    try:
        result = function(run, *args)
    except PayrollError as exc:
        messages.error(request, str(exc))
        return None
    messages.success(request, success(result) if callable(success) else success)
    return result


# ------------------------------------------------------------------ policy

@hr_perm("payroll.view_payrollpolicy")
def policy_list(request):
    policies = PayrollPolicy.objects.for_user(request.user).select_related("company")
    return render(request, "web/payroll/policy.html", {
        "policies": policies, "multi_company": companies_for(request.user).count() > 1})


@hr_perm("payroll.add_payrollpolicy")
def policy_new(request):
    form = PolicyForm(request.POST or None, user=request.user)
    if request.method == "POST" and form.is_valid():
        cd = form.cleaned_data
        try:
            set_policy(cd["company"], effective_from=cd["effective_from"], days_in_month=cd["days_in_month"],
                       sio_employee_rate=cd["sio_employee_rate"], sio_employer_rate=cd["sio_employer_rate"],
                       sio_employer_rate_other=cd["sio_employer_rate_other"], sio_wage_floor=cd["sio_wage_floor"],
                       sio_wage_cap=cd["sio_wage_cap"], sio_on_paid_days=cd["sio_on_paid_days"])
        except PayrollError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "Payroll policy saved. The earlier policy is kept in the history.")
            return redirect("web:payroll_policy")
    return _form_page(request, form, "Payroll policy", reverse("web:payroll_policy"), "policy",
                      intro="The current policy ends the day before the new one starts. Leave the SIO rates empty "
                            "until the accountant gives them: payroll then stops at any Bahraini rather than guess.")


# ------------------------------------------------------------------ runs

@hr_perm("payroll.view_payrollrun")
def run_list(request):
    company = request.GET.get("company", "")
    companies = companies_for(request.user)
    qs = PayrollRun.objects.for_user(request.user).select_related("company", "prepared_by").annotate(
        people=Count("lines"))
    if company.isdigit():
        qs = qs.filter(company_id=company)
    rows = []
    for run in qs:
        rows.append({"run": run, "totals": runs.totals(run)})
    return render(request, "web/payroll/runs.html", {
        "rows": rows, "companies": companies, "company": company, "multi_company": companies.count() > 1})


@hr_perm("payroll.add_payrollrun")
def run_create(request):
    form = RunForm(request.POST or None, user=request.user)
    if request.method == "POST" and form.is_valid():
        cd = form.cleaned_data
        try:
            run = runs.create_run(cd["company"], cd["year"], cd["month"], user=request.user, kind=cd["kind"])
        except PayrollError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, f"{run.get_kind_display()} {run.label} was calculated. Check the people marked "
                                      "in red or amber, then send it for approval.")
            return redirect(_url(run))
    return _form_page(request, form, "New payroll run", reverse("web:payroll_runs"), "run",
                      intro="A payroll run for one company and one month. It is calculated straight away, and you can "
                            "recalculate as often as you like until it is sent for approval. A direct labor run needs "
                            "confirmed hours and settled overtime for the whole month.")


@hr_perm("payroll.view_payrollrun")
def run_detail(request, pk):
    run = _run(request, pk)
    lines = list(run.lines.all())
    return render(request, "web/payroll/run.html", {
        "run": run, "lines": lines, "totals": runs.totals(run),
        "no_profile": labor_payroll.without_profile(run) if run.kind == run.Kind.LABOR else [],
        "adjustments": list(run.adjustments.select_related("employee")),
        "candidates": len(runs.charge_candidates(run, request.user)) if run.status == run.Status.DRAFT
        and request.user.has_perm("payroll.add_payrollrun") else 0,
        "cash_unpaid": run.lines.filter(payment_method="cash", paid_on__isnull=True).count(),
        "bank_unpaid": run.lines.filter(payment_method="bank", paid_on__isnull=True).count()})


@hr_perm("payroll.add_payrollrun")
@require_POST
def run_recalculate(request, pk):
    run = _run(request, pk)
    _act(request, run, runs.calculate_run, "Recalculated from the pay records as they are now.", request.user)
    return redirect(_url(run))


@hr_perm("payroll.add_payrollrun")
@require_POST
def run_cancel(request, pk):
    run = _run(request, pk)
    if _act(request, run, runs.cancel_run, "The run was cancelled."):
        return redirect("web:payroll_runs")
    return redirect(_url(run))


@hr_perm("payroll.submit_payrollrun")
@require_POST
def run_submit(request, pk):
    run = _run(request, pk)
    _act(request, run, runs.submit_run, "Sent to Management. They will see it in their approvals inbox.", request.user)
    return redirect(_url(run))


@hr_perm("payroll.submit_payrollrun")
@require_POST
def run_withdraw(request, pk):
    run = _run(request, pk)
    _act(request, run, runs.withdraw_run, "Taken back. You can change it and send it again.", request.user)
    return redirect(_url(run))


@hr_perm("payroll.add_payrollrun")
@require_POST
def run_import_charges(request, pk):
    run = _run(request, pk)
    _act(request, run, runs.import_charges,
         lambda n: (f"{n} charge{'s' if n != 1 else ''} added as deductions." if n else "There is nothing to add."),
         request.user)
    return redirect(_url(run))


@hr_perm("payroll.add_payrollrun")
def adjustment_add(request, pk):
    run = _run(request, pk)
    if run.status != run.Status.DRAFT:
        messages.error(request, "Only a draft run can be adjusted.")
        return redirect(_url(run))
    form = AdjustmentForm(request.POST or None, run=run)
    if request.method == "POST" and form.is_valid():
        cd = form.cleaned_data
        try:
            runs.add_adjustment(run, cd["employee"], kind=cd["kind"], category=cd["category"], label=cd["label"],
                                amount=cd["amount"])
        except PayrollError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "Added.")
            return redirect(_url(run))
    return _form_page(request, form, f"Adjustment: {run.label}", _url(run), "adjustment",
                      intro="A bonus or incentive adds to the pay; a salary advance or penalty takes from it. "
                            "Renewal fees and vehicle fines charged to a person are picked up with one button on the run.")


@hr_perm("payroll.add_payrollrun")
@require_POST
def adjustment_remove(request, pk, adjustment_pk):
    run = _run(request, pk)
    adjustment = get_object_or_404(PayrollAdjustment.objects.select_related("employee"), pk=adjustment_pk, run=run)
    _act(request, adjustment, lambda a: runs.remove_adjustment(a), "Removed.")
    return redirect(_url(run))


@hr_perm("payroll.view_payrollrun")
def line_detail(request, pk, line_pk):
    run = _run(request, pk)
    line = get_object_or_404(PayrollLine.objects.select_related("employee"), pk=line_pk, run=run)
    return render(request, "web/payroll/line.html", {
        "run": run, "line": line, "adjustments": list(run.adjustments.filter(employee=line.employee))})


@hr_perm("payroll.pay_payrollrun")
def run_pay(request, pk, method):
    run = _run(request, pk)
    if method not in ("bank", "cash"):
        raise Http404
    if run.status != run.Status.APPROVED:
        messages.error(request, "Payments are recorded once the run is approved.")
        return redirect(_url(run))
    form = PayForm(request.POST or None, run=run, method=method)
    if request.method == "POST" and form.is_valid():
        cd = form.cleaned_data
        try:
            n = runs.mark_paid(run, [line.pk for line in cd["lines"]], cd["paid_on"])
        except PayrollError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, f"{n} payment{'s' if n != 1 else ''} recorded.")
            return redirect(_url(run))
    what = "Bank transfers" if method == "bank" else "Cash payments"
    return _form_page(request, form, f"{what}: {run.label}", _url(run), "pay",
                      intro="Untick anyone who has not been paid yet. People already marked as paid are not listed.")


def _issue_payslips(request, run, lines):
    """Show payslips ready to print. Only for a run that is approved or paid; every view is audited."""
    from apps.audit import services as audit

    adjustments = {}
    for a in run.adjustments.all():
        adjustments.setdefault(a.employee_id, []).append(a)
    for line in lines:
        audit.log("download", line, module="payroll", subject_employee_id=line.employee_id, company_id=run.company_id,
                  changes={"payslip": run.label})
    return render(request, "web/payroll/payslip.html", {
        "run": run, "slips": [{"line": l, "adjustments": adjustments.get(l.employee_id, [])} for l in lines]})


@hr_perm("payroll.view_payrollrun")
def payslips(request, pk):
    run = _run(request, pk)
    if not run.frozen:
        messages.error(request, "Payslips are issued once the run is approved.")
        return redirect(_url(run))
    return _issue_payslips(request, run, list(run.lines.all()))


@hr_perm("payroll.view_payrollrun")
def payslip(request, pk, line_pk):
    run = _run(request, pk)
    if not run.frozen:
        messages.error(request, "Payslips are issued once the run is approved.")
        return redirect(_url(run))
    return _issue_payslips(request, run, [get_object_or_404(PayrollLine, pk=line_pk, run=run)])


@hr_perm("payroll.view_payrollrun")
def run_export(request, pk, kind):
    run = _run(request, pk)
    if kind not in outputs.BUILDERS:
        raise Http404
    response = HttpResponse(outputs.BUILDERS[kind](run), content_type=XLSX)
    name = slugify(f"payroll-{kind}-{run.company.code}-{run.year}-{run.month:02d}")
    response["Content-Disposition"] = f'attachment; filename="{name}.xlsx"'
    return response
