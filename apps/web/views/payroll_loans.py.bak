"""Payroll > Loans: the list, a new loan, one loan with its schedule and payments, and what HR can do with it."""
from datetime import date

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import DecimalField, OuterRef, Q, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.cache import patch_vary_headers
from django.views.decorators.http import require_POST

from apps.employees.models import Employee
from apps.organization.services import companies_for
from apps.payroll import loan_services as service
from apps.payroll import loan_settings
from apps.payroll.loans import Loan, LoanInstalment, LoanPayment
from apps.payroll.services import PayrollError

from apps.web.access import hr_perm
from apps.web.forms.payroll_loans import (LoanForm, PaymentForm, PayoutForm, PrecloseForm, ReasonForm,
                                          RescheduleForm, month_value)

PICK_LIMIT = 10
Status = Loan.Status
LAYOUTS = {
    "payout": [{"title": "Bank transfer", "width": "col-lg-6", "rows": [["paid_on"], ["reference"]]}],
    "payment": [{"title": "Repayment", "width": "col-lg-6", "rows": [["amount"], ["paid_on", "method"], ["reference"]]}],
    "preclose": [{"title": "Pay off the loan", "width": "col-lg-6", "rows": [["paid_on", "method"], ["reference"]]}],
    "reschedule": [{"title": "New schedule", "width": "col-lg-6", "rows": [["months", "first_month"]]}],
    "reason": [{"title": "Reason", "width": "col-lg-6", "rows": [["reason"]]}],
}


def _form_page(request, form, title, back_url, layout, intro="", submit_label="Save"):
    return render(request, "web/form_layout.html", {
        "form": form, "title": title, "back_url": back_url, "layout": LAYOUTS[layout], "intro": intro,
        "submit_label": submit_label})


def _loan(request, pk):
    return get_object_or_404(Loan.objects.for_user(request.user).select_related("employee", "company"), pk=pk)


def _url(loan):
    return reverse("web:payroll_loan", args=[loan.pk])


def _act(request, function, success, *args, **kwargs):
    """Run a loan action from a button. A broken rule is shown as a message, never as a crash."""
    try:
        result = function(*args, **kwargs)
    except PayrollError as exc:
        messages.error(request, str(exc))
        return None
    messages.success(request, success(result) if callable(success) else success)
    return result


def _employee(request, raw):
    """The person a new loan is for: someone the user can see, still with the company."""
    if not str(raw or "").isdigit():
        return None
    return (Employee.objects.for_user(request.user).exclude(status=Employee.Status.SEPARATED)
            .select_related("company").filter(pk=int(raw)).first())


# ------------------------------------------------------------------ the list and the picker

@hr_perm("payroll.view_loan")
def loan_list(request):
    q, status = request.GET.get("q", "").strip(), request.GET.get("status", "open")
    zero = Value(0, output_field=DecimalField(max_digits=12, decimal_places=3))
    # two sub-queries, not two joins: joining payments and instalments together would multiply the repaid amount
    repaid = (LoanPayment.objects.filter(loan=OuterRef("pk")).order_by().values("loan")
              .annotate(total=Sum("amount")).values("total"))
    upcoming = (LoanInstalment.objects.filter(loan=OuterRef("pk"), status="scheduled").order_by("due_month", "id")
                .values("due_month")[:1])
    qs = (Loan.objects.for_user(request.user).select_related("employee", "company")
          .annotate(repaid_sum=Coalesce(Subquery(repaid, output_field=DecimalField(max_digits=12, decimal_places=3)), zero),
                    next_month=Subquery(upcoming)))
    if status == "open":
        qs = qs.filter(status__in=[Status.DRAFT, Status.SUBMITTED, Status.APPROVED, Status.ACTIVE])
    elif status in Status.values:
        qs = qs.filter(status=status)
    for word in q.split():
        qs = qs.filter(Q(employee__first_name__icontains=word) | Q(employee__last_name__icontains=word)
                       | Q(employee__employee_no__icontains=word))
    page = Paginator(qs, 25).get_page(request.GET.get("page"))
    for loan in page:
        loan.balance_now = loan.amount + loan.interest_total - loan.repaid_sum
    params = request.GET.copy()
    params.pop("page", None)
    return render(request, "web/payroll/loans.html", {
        "page": page, "q": q, "status": status, "statuses": Status.choices, "extra": params.urlencode(),
        "multi_company": companies_for(request.user).count() > 1})


@hr_perm("payroll.add_loan")
def loan_pick(request):
    """Search first: nothing is listed until 2 letters are typed, and never more than the best matches."""
    q = " ".join(request.GET.get("q", "").split())
    words = q.split()
    rows, hint, more = [], "", False
    if not any(len(w) >= 2 for w in words):
        hint = "Type at least 2 letters of the name, or part of the employee number."
    else:
        qs = Employee.objects.for_user(request.user).exclude(status=Employee.Status.SEPARATED)
        for word in words:
            qs = qs.filter(Q(first_name__icontains=word) | Q(last_name__icontains=word) | Q(employee_no__icontains=word))
        rows = list(qs.select_related("company").order_by("employee_no")[:PICK_LIMIT + 1])
        more, rows = len(rows) > PICK_LIMIT, rows[:PICK_LIMIT]
        if not rows:
            hint = "Nobody matches that."
    ctx = {"q": q, "rows": rows, "hint": hint, "more": more, "limit": PICK_LIMIT}
    partial = request.headers.get("HX-Request") == "true" and not request.headers.get("HX-History-Restore-Request")
    resp = render(request, "web/payroll/_loan_pick_results.html" if partial else "web/payroll/loan_pick.html", ctx)
    patch_vary_headers(resp, ["HX-Request"])
    return resp


# ------------------------------------------------------------------ a new loan, and changing a draft

def _preview_context(form):
    return {"default_first": month_value(service.add_months(timezone.localdate().replace(day=1), 1))}


@hr_perm("payroll.add_loan")
def loan_new(request):
    employee = _employee(request, request.GET.get("employee") or request.POST.get("employee"))
    if employee is None:
        return redirect("web:payroll_loan_pick")
    can_verbal = request.user.has_perm("payroll.agree_loan")
    initial = {"kind": "loan", "months": 12, "interest_rate": loan_settings.default_interest(employee.company),
               "first_month": month_value(service.add_months(timezone.localdate().replace(day=1), 1)),
               "how": "approval", "agreed_on": timezone.localdate()}
    form = LoanForm(request.POST or None, initial=initial, can_verbal=can_verbal)
    warnings = []
    if request.method == "POST" and form.is_valid():
        cd = form.cleaned_data
        try:
            loan = service.create_loan(
                request.user, employee, kind=cd["kind"], amount=cd["amount"], months=cd["months"],
                first_month=cd["first_month"], interest_rate=cd["interest_rate"], reason=cd["reason"],
                verbal=form.verbal, agreed_by=cd.get("agreed_by", ""), agreed_on=cd.get("agreed_on"),
                override_reason=cd.get("override_reason", ""))
        except service.LimitError as exc:
            warnings = exc.warnings
            form.add_error("override_reason", "Give the reason to go ahead.")
        except PayrollError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, f"{loan.number} was created" + (" as approved." if form.verbal else "."))
            return redirect(_url(loan))
    return render(request, "web/payroll/loan_form.html", {
        "form": form, "employee": employee, "loan": None, "warnings": warnings, "can_verbal": can_verbal,
        "back_url": reverse("web:payroll_loans")})


@hr_perm("payroll.add_loan")
def loan_edit(request, pk):
    loan = _loan(request, pk)
    if loan.status != Status.DRAFT:
        messages.error(request, "Only a draft loan can be changed.")
        return redirect(_url(loan))
    initial = {"kind": loan.kind, "amount": loan.amount, "months": loan.months, "interest_rate": loan.interest_rate,
               "first_month": month_value(loan.first_month), "reason": loan.reason,
               "override_reason": loan.override_reason}
    form = LoanForm(request.POST or None, initial=initial, editing=True)
    warnings = []
    if request.method == "POST" and form.is_valid():
        cd = form.cleaned_data
        try:
            service.update_draft(request.user, loan, kind=cd["kind"], amount=cd["amount"], months=cd["months"],
                                 first_month=cd["first_month"], interest_rate=cd["interest_rate"], reason=cd["reason"],
                                 override_reason=cd.get("override_reason", ""))
        except service.LimitError as exc:
            warnings = exc.warnings
            form.add_error("override_reason", "Give the reason to go ahead.")
        except PayrollError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "Saved.")
            return redirect(_url(loan))
    return render(request, "web/payroll/loan_form.html", {
        "form": form, "employee": loan.employee, "loan": loan, "warnings": warnings, "can_verbal": False,
        "back_url": _url(loan)})


# ------------------------------------------------------------------ one loan

@hr_perm("payroll.view_loan")
def loan_detail(request, pk):
    loan = _loan(request, pk)
    user = request.user
    rows = list(loan.instalments.exclude(status=LoanInstalment.Status.REPLACED).order_by("due_month", "id"))
    live = [r for r in rows if r.status != LoanInstalment.Status.SKIPPED]
    number = {r.pk: n for n, r in enumerate(live, 1)}
    for r in rows:
        r.n = number.get(r.pk)
    next_row = next((r for r in rows if r.status == LoanInstalment.Status.SCHEDULED), None)
    preview = []
    if loan.status in (Status.DRAFT, Status.SUBMITTED):
        preview = [(m, a) for m, a in service.preview(loan.amount, loan.months, loan.first_month, loan.interest_rate)]
    return render(request, "web/payroll/loan.html", {
        "loan": loan, "rows": rows, "preview": preview, "next_row": next_row, "payments": list(loan.payments.all()),
        "balance": loan.balance, "repaid": loan.repaid,
        "left": loan.employee.status == Employee.Status.SEPARATED,
        "can_add": user.has_perm("payroll.add_loan"), "can_submit": user.has_perm("payroll.submit_loan"),
        "can_pay": user.has_perm("payroll.pay_loan")})


@hr_perm("payroll.view_loan")
def loan_statement(request, pk):
    loan = _loan(request, pk)
    return render(request, "web/payroll/loan_statement.html", {
        "loan": loan, "rows": list(loan.instalments.exclude(status=LoanInstalment.Status.REPLACED)),
        "payments": list(loan.payments.all()), "balance": loan.balance, "repaid": loan.repaid,
        "today": timezone.localdate()})


@hr_perm("payroll.submit_loan")
@require_POST
def loan_submit(request, pk):
    loan = _loan(request, pk)
    _act(request, service.submit_loan, "Sent to Management for approval.", loan, request.user)
    return redirect(_url(loan))


@hr_perm("payroll.submit_loan")
@require_POST
def loan_withdraw(request, pk):
    loan = _loan(request, pk)
    _act(request, service.withdraw_loan, "Taken back for changes.", loan, request.user)
    return redirect(_url(loan))


@hr_perm("payroll.add_loan")
def loan_cancel(request, pk):
    loan = _loan(request, pk)
    form = ReasonForm(request.POST or None)
    if loan.status == Status.DRAFT and request.method == "POST":           # a draft needs no reason
        _act(request, service.cancel_loan, "The loan was cancelled.", request.user, loan)
        return redirect("web:payroll_loans")
    if request.method == "POST" and form.is_valid():
        if _act(request, service.cancel_loan, "The loan was cancelled.", request.user, loan, form.cleaned_data["reason"]):
            return redirect("web:payroll_loans")
    if loan.status == Status.DRAFT:
        return render(request, "web/payroll/loan_confirm.html", {"loan": loan, "back_url": _url(loan)})
    return _form_page(request, form, f"Cancel {loan.number}", _url(loan), "reason",
                      "The loan was approved but not paid out. Say why it is cancelled.", "Cancel the loan")


@hr_perm("payroll.pay_loan")
def loan_payout(request, pk):
    loan = _loan(request, pk)
    if loan.status != Status.APPROVED:
        messages.error(request, "Only an approved loan can be paid out.")
        return redirect(_url(loan))
    form = PayoutForm(request.POST or None, initial={"paid_on": timezone.localdate()})
    if request.method == "POST" and form.is_valid():
        cd = form.cleaned_data
        try:
            service.record_payout(request.user, loan, cd["paid_on"], cd["reference"])
        except PayrollError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "The payout was recorded. The instalments are now picked up by payroll.")
            return redirect(_url(loan))
    return _form_page(request, form, f"Record the payout of {loan.number}", _url(loan), "payout",
                      f"{loan.employee.full_name}: {loan.amount:,.3f} BHD by bank transfer.", "Record the payout")


@hr_perm("payroll.pay_loan")
def loan_payment(request, pk):
    loan = _loan(request, pk)
    if loan.status != Status.ACTIVE:
        messages.error(request, "Repayments are recorded for an active loan.")
        return redirect(_url(loan))
    form = PaymentForm(request.POST or None, initial={"paid_on": timezone.localdate(), "method": "bank"})
    if request.method == "POST" and form.is_valid():
        cd = form.cleaned_data
        try:
            service.record_payment(request.user, loan, cd["amount"], cd["paid_on"], cd["method"], cd["reference"])
        except PayrollError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "The repayment was recorded. The last instalments are shortened to match.")
            return redirect(_url(loan))
    return _form_page(request, form, f"Early payment on {loan.number}", _url(loan), "payment",
                      f"The balance is {loan.balance:,.3f} BHD. The payment shortens the schedule from the far end.",
                      "Record the repayment")


@hr_perm("payroll.pay_loan")
def loan_preclose(request, pk):
    loan = _loan(request, pk)
    if loan.status != Status.ACTIVE:
        messages.error(request, "Only an active loan can be paid off.")
        return redirect(_url(loan))
    form = PrecloseForm(request.POST or None, initial={"paid_on": timezone.localdate(), "method": "bank"})
    if request.method == "POST" and form.is_valid():
        cd = form.cleaned_data
        try:
            service.pre_close(request.user, loan, cd["paid_on"], cd["method"], cd["reference"])
        except PayrollError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "The loan was paid off and is closed.")
            return redirect(_url(loan))
    return _form_page(request, form, f"Pay off {loan.number}", _url(loan), "preclose",
                      f"The whole balance of {loan.balance:,.3f} BHD is paid now.", "Pay off the loan")


@hr_perm("payroll.pay_loan")
@require_POST
def loan_skip(request, pk):
    loan = _loan(request, pk)
    _act(request, service.skip_next, "The next instalment was moved to the end.", request.user, loan)
    return redirect(_url(loan))


@hr_perm("payroll.pay_loan")
def loan_reschedule(request, pk):
    loan = _loan(request, pk)
    if loan.status != Status.ACTIVE:
        messages.error(request, "Only an active loan can be rescheduled.")
        return redirect(_url(loan))
    first = service.add_months(timezone.localdate().replace(day=1), 1)
    form = RescheduleForm(request.POST or None, initial={"months": 6, "first_month": month_value(first)})
    if request.method == "POST" and form.is_valid():
        cd = form.cleaned_data
        try:
            service.reschedule(request.user, loan, cd["months"], cd["first_month"])
        except PayrollError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "The rest of the loan has a new schedule.")
            return redirect(_url(loan))
    return _form_page(request, form, f"Reschedule {loan.number}", _url(loan), "reschedule",
                      f"The balance of {loan.balance:,.3f} BHD is spread again. Instalments already deducted stay as they are.",
                      "Make the new schedule")


@hr_perm("payroll.pay_loan")
def loan_waive(request, pk):
    loan = _loan(request, pk)
    if loan.status != Status.ACTIVE:
        messages.error(request, "Only an active loan can be waived.")
        return redirect(_url(loan))
    form = ReasonForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        if _act(request, service.waive, "The balance was waived and the loan is closed.", request.user, loan,
                form.cleaned_data["reason"]):
            return redirect(_url(loan))
    return _form_page(request, form, f"Waive the balance of {loan.number}", _url(loan), "reason",
                      f"{loan.balance:,.3f} BHD will not be recovered. The reason is kept with the loan.", "Waive the balance")
