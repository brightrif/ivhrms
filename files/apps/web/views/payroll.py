from urllib.parse import urlencode

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Prefetch, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.employees.models import Employee
from apps.organization.services import companies_for
from apps.payroll import services
from apps.payroll.models import AllowanceType, SalaryRecord
from apps.payroll.services import PayrollError

from apps.web.access import hr_perm
from apps.web.forms.payroll import AllowanceTypeForm, PaymentForm, SalaryChangeForm, SalaryCorrectForm

# Full-page layouts, side by side on a laptop screen so nothing needs scrolling. See form_layout.html.
LAYOUTS = {
    "allowance": [{"title": "Allowance", "width": "col-lg-6",
                   "rows": [["company"], ["name"], ["counts_for_sio"]]}],
    "payment": [{"title": "How this person is paid", "width": "col-lg-7",
                 "rows": [["method"], ["bank_name", "iban"]]}],
}
PAGE_SIZE = 25


def _pairs(names):
    return [names[i:i + 2] for i in range(0, len(names), 2)]


def _salary_layout(form, first):
    """The salary boxes on the left, the allowance boxes beside them. No allowance types means no second card."""
    layout = [first]
    if form.allowance_names:
        layout.append({"title": "Allowances", "width": "col-lg-7", "rows": _pairs(form.allowance_names)})
    return layout


def _form_page(request, form, title, back_url, layout, intro="", submit_label="Save"):
    return render(request, "web/form_layout.html", {
        "form": form, "title": title, "back_url": back_url, "layout": layout, "intro": intro,
        "submit_label": submit_label})


def _person(request, pk):
    """Anyone payroll covers whom this user may see, including people who have left (their history stays readable)."""
    qs = services.in_payroll(request.user, include_separated=True).select_related("company")
    return get_object_or_404(qs, pk=pk)


def _staff(request, pk):
    return get_object_or_404(services.in_payroll(request.user, include_separated=True)
                             .filter(worker_type=Employee.WorkerType.STAFF).select_related("company"), pk=pk)


def _page_url(emp):
    return reverse("web:payroll_employee", args=[emp.pk])


def _in_force(records, today):
    return next((r for r in records if r.effective_from <= today and (r.effective_to is None or r.effective_to >= today)),
                None)


# ------------------------------------------------------------------ the people list

@hr_perm("payroll.view_salaryrecord")
def payroll_home(request):
    today = timezone.localdate()
    q = request.GET.get("q", "").strip()
    company = request.GET.get("company", "")
    kind = request.GET.get("kind", "")
    show = request.GET.get("show", "")
    companies = companies_for(request.user)

    people = services.with_coverage(services.in_payroll(request.user), today)
    if q:
        people = people.filter(Q(first_name__icontains=q) | Q(last_name__icontains=q) | Q(employee_no__icontains=q))
    if company.isdigit():
        people = people.filter(company_id=company)
    if kind in (Employee.WorkerType.STAFF, Employee.WorkerType.LABOR):
        people = people.filter(worker_type=kind)
    if show == "no_salary":
        people = people.filter(worker_type=Employee.WorkerType.STAFF, has_salary=False)
    elif show == "no_payment":
        people = people.filter(has_payment=False)
    people = (people.select_related("company", "payment_detail")
              .prefetch_related(Prefetch("salary_records", queryset=SalaryRecord.objects
                                         .prefetch_related("allowances").order_by("-effective_from")))
              .order_by("company__code", "employee_no"))

    page = Paginator(people, PAGE_SIZE).get_page(request.GET.get("page"))
    for person in page:
        person.salary_now = _in_force(list(person.salary_records.all()), today)
    filters = {k: v for k, v in (("q", q), ("company", company), ("kind", kind), ("show", show)) if v}
    return render(request, "web/payroll/home.html", {
        "page": page, "q": q, "company": company, "kind": kind, "show": show, "companies": companies,
        "multi_company": companies.count() > 1, "gaps": services.coverage(request.user, today),
        "extra": urlencode(filters)})


# ------------------------------------------------------------------ one person

@hr_perm("payroll.view_salaryrecord")
def payroll_employee(request, pk):
    emp = _person(request, pk)
    today = timezone.localdate()
    is_staff = emp.worker_type == Employee.WorkerType.STAFF
    history = list(services.salary_history(emp)) if is_staff else []
    return render(request, "web/payroll/employee.html", {
        "emp": emp, "is_staff": is_staff, "history": history,
        "current": _in_force(history, today),
        "upcoming": next((r for r in reversed(history) if r.effective_from > today), None),
        "latest": next((r for r in history if r.effective_to is None), None),
        "payment": getattr(emp, "payment_detail", None),
        "labor_profile": getattr(emp, "labor_profile", None),
        "can_change": emp.status != Employee.Status.SEPARATED})


@hr_perm("payroll.add_salaryrecord")
def salary_change(request, pk):
    emp = _staff(request, pk)
    back = _page_url(emp)
    if emp.status == Employee.Status.SEPARATED:
        messages.error(request, f"{emp.full_name} has left the company, so there is no new salary to set.")
        return redirect(back)
    latest = services.salary_history(emp).filter(effective_to__isnull=True).first()
    form = SalaryChangeForm(request.POST or None, employee=emp, latest=latest)
    if request.method == "POST" and form.is_valid():
        cd = form.cleaned_data
        try:
            services.change_salary(emp, effective_from=cd["effective_from"], basic=cd["basic"],
                                   allowances=form.allowances(), reason=cd["reason"], remarks=cd["remarks"])
        except PayrollError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "New salary saved. The earlier salary is kept in the history.")
            return redirect(back)
    first = {"title": "Salary", "width": "col-lg-5",
             "rows": [["effective_from", "reason"], ["basic"], ["remarks"]]}
    intro = ("The current salary ends the day before the new one starts." if latest else
             "This is the first salary on record for this person.")
    if not form.allowance_names:
        intro += " This company has no allowance types yet. Add them in Settings, under Payroll."
    return _form_page(request, form, f"Salary: {emp.full_name}", back, _salary_layout(form, first), intro)


@hr_perm("payroll.change_salaryrecord")
def salary_correct(request, pk):
    emp = _staff(request, pk)
    back = _page_url(emp)
    record = services.salary_history(emp).filter(effective_to__isnull=True).first()
    if record is None:
        messages.error(request, "There is no salary to correct yet.")
        return redirect(back)
    form = SalaryCorrectForm(request.POST or None, record=record)
    if request.method == "POST" and form.is_valid():
        cd = form.cleaned_data
        try:
            services.correct_salary(record, basic=cd["basic"], allowances=form.allowances(), remarks=cd["remarks"])
        except PayrollError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "The salary was corrected.")
            return redirect(back)
    first = {"title": "Correction", "width": "col-lg-5", "rows": [["basic"], ["remarks"]]}
    return _form_page(request, form, f"Correct salary: {emp.full_name}", back, _salary_layout(form, first),
                      intro=f"This changes the salary that started on {record.effective_from:%d %b %Y}, in place. "
                            "For a raise, use Change salary instead.")


@hr_perm("payroll.add_salaryrecord")
def payment_edit(request, pk):
    emp = _person(request, pk)
    back = _page_url(emp)
    detail = getattr(emp, "payment_detail", None)
    form = PaymentForm(request.POST or None, detail=detail)
    if request.method == "POST" and form.is_valid():
        cd = form.cleaned_data
        try:
            services.save_payment_details(emp, method=cd["method"], bank_name=cd["bank_name"], iban=cd["iban"])
        except PayrollError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "Payment details saved.")
            return redirect(back)
    return _form_page(request, form, f"Payment details: {emp.full_name}", back, LAYOUTS["payment"],
                      intro="Cash needs nothing more. A bank transfer needs the bank and the IBAN.")


# ------------------------------------------------------------------ allowance types

@hr_perm("payroll.view_allowancetype")
def allowance_list(request):
    types = list(AllowanceType.objects.for_user(request.user).select_related("company"))
    for allowance in types:
        allowance.receiving = services.people_receiving(allowance)
    return render(request, "web/payroll/allowances.html", {
        "types": types, "multi_company": companies_for(request.user).count() > 1})


@hr_perm("payroll.add_allowancetype")
def allowance_create(request):
    form = AllowanceTypeForm(request.POST or None, user=request.user)
    if request.method == "POST" and form.is_valid():
        cd = form.cleaned_data
        try:
            services.create_allowance_type(cd["company"], name=cd["name"], counts_for_sio=cd["counts_for_sio"])
        except PayrollError as exc:
            form.add_error("name", str(exc))
        else:
            messages.success(request, f"{cd['name']} was added.")
            return redirect("web:payroll_allowances")
    return _form_page(request, form, "New allowance", reverse("web:payroll_allowances"), LAYOUTS["allowance"])


@hr_perm("payroll.add_allowancetype")
def allowance_edit(request, pk):
    allowance = get_object_or_404(AllowanceType.objects.for_user(request.user), pk=pk)
    form = AllowanceTypeForm(request.POST or None, user=request.user, allowance=allowance)
    if request.method == "POST" and form.is_valid():
        cd = form.cleaned_data
        try:
            services.update_allowance_type(allowance, name=cd["name"], counts_for_sio=cd["counts_for_sio"])
        except PayrollError as exc:
            form.add_error("name", str(exc))
        else:
            messages.success(request, "Allowance updated.")
            return redirect("web:payroll_allowances")
    return _form_page(request, form, f"Allowance: {allowance.name}", reverse("web:payroll_allowances"),
                      LAYOUTS["allowance"])


@hr_perm("payroll.add_allowancetype")
@require_POST
def allowance_toggle(request, pk):
    allowance = get_object_or_404(AllowanceType.objects.for_user(request.user), pk=pk)
    try:
        services.set_allowance_active(allowance, not allowance.is_active)
    except PayrollError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f"{allowance.name} is now {'on' if not allowance.is_active else 'off'}.")
    return redirect("web:payroll_allowances")
