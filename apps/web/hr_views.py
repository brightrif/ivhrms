from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.cache import patch_vary_headers
from django.views.decorators.http import require_POST

from apps.employees import services
from apps.employees.models import Employee
from apps.organization.services import companies_for

from django.http import HttpResponse
from django.utils.html import format_html

from apps.employees import numbering


from .hr_forms import ASSIGNMENT_FIELDS, AssignmentForm, EmployeeForm, EmployeePersonalForm


def hr_perm(perm):
    def decorator(view):
        return login_required(permission_required(perm, raise_exception=True)(view))
    return decorator


def _scoped_employee(request, pk):
    """Only employees in companies this user may access; anything else is a 404."""
    qs = Employee.objects.for_user(request.user).select_related(
        "company", "department", "designation", "grade", "location", "reporting_manager", "user")
    return get_object_or_404(qs, pk=pk)


@hr_perm("employees.view_employee")
def employee_list(request):
    companies = companies_for(request.user)
    qs = Employee.objects.for_user(request.user).select_related("company", "department", "designation")
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(employee_no__icontains=q) | Q(first_name__icontains=q) | Q(last_name__icontains=q)
                       | Q(name_ar__icontains=q) | Q(email__icontains=q))
    company, status, wtype = (request.GET.get(k, "") for k in ("company", "status", "worker_type"))
    if company.isdigit():
        qs = qs.filter(company_id=int(company))
    if status:
        qs = qs.filter(status=status)
    if wtype:
        qs = qs.filter(worker_type=wtype)

    params = request.GET.copy()
    params.pop("page", None)
    ctx = {
        "page": Paginator(qs, 25).get_page(request.GET.get("page")), "extra": params.urlencode(),
        "q": q, "company": company, "status": status, "wtype": wtype,
        "companies": companies, "multi_company": companies.count() > 1,
        "statuses": Employee.Status.choices, "worker_types": Employee.WorkerType.choices,
    }
    partial = request.headers.get("HX-Request") == "true" and not request.headers.get("HX-History-Restore-Request")
    resp = render(request, "web/_employee_table.html" if partial else "web/employee_list.html", ctx)
    patch_vary_headers(resp, ["HX-Request"])
    return resp


@hr_perm("employees.add_employee")
def employee_create(request):
    if request.method == "POST":
        form = EmployeeForm(request.POST, user=request.user)
        if form.is_valid():
            cd = form.cleaned_data
            try:
                employee, credentials = services.create_employee(
                    form.save(commit=False), with_login=cd["create_login"],
                    username=cd.get("username") or None)
            except services.EmployeeError as exc:
                form.add_error(None, str(exc))
            else:
                if credentials:
                    request.session["new_login"] = {"employee": employee.pk, **credentials}
                messages.success(request, f"{employee.full_name} was added as {employee.employee_no}.")
                return redirect("web:employee_detail", pk=employee.pk)
    else:
        form = EmployeeForm(user=request.user, initial={"joining_date": timezone.localdate()})
    return render(request, "web/employee_form.html", {"form": form})


@hr_perm("employees.add_employee")
def employee_company_fields(request):
    """HTMX: the company-dependent dropdowns (department, manager, shift)."""
    form = EmployeeForm(user=request.user, company_id=request.GET.get("company"))
    return render(request, "web/_employee_company_fields.html", {"form": form})


@hr_perm("employees.view_employee")
def employee_detail(request, pk):
    emp = _scoped_employee(request, pk)
    credentials = request.session.get("new_login")
    if credentials and credentials.get("employee") == emp.pk:
        request.session.pop("new_login")            # shown once, then gone
    else:
        credentials = None
    history = emp.history.select_related("department", "designation", "reporting_manager")
    return render(request, "web/employee_detail.html",
                  {"employee": emp, "history": history, "credentials": credentials})


@hr_perm("employees.change_employee")
def employee_edit(request, pk):
    emp = _scoped_employee(request, pk)
    old_number = emp.whatsapp_number
    form = EmployeePersonalForm(request.POST or None, instance=emp)
    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False)
        if obj.whatsapp_number != old_number:
            obj.whatsapp_verified = False           # a new number must be verified again
        obj.save()
        messages.success(request, "Details updated.")
        return redirect("web:employee_detail", pk=emp.pk)
    return render(request, "web/form_page.html", {
        "form": form, "title": f"Edit details: {emp.full_name}",
        "back_url": reverse("web:employee_detail", args=[emp.pk])})


@hr_perm("employees.change_employee")
def employee_assign(request, pk):
    emp = _scoped_employee(request, pk)
    form = AssignmentForm(request.POST or None, employee=emp)
    if request.method == "POST" and form.is_valid():
        cd = form.cleaned_data
        try:
            services.record_assignment(
                emp, effective_from=cd["effective_from"], reason=cd["reason"], remarks=cd["remarks"],
                **{f: cd[f] for f in ASSIGNMENT_FIELDS})
        except ValueError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "Assignment updated and added to the history.")
            return redirect("web:employee_detail", pk=emp.pk)
    return render(request, "web/form_page.html", {
        "form": form, "title": f"Change assignment: {emp.full_name}",
        "back_url": reverse("web:employee_detail", args=[emp.pk])})


@hr_perm("employees.manage_logins")
@require_POST
def employee_login(request, pk):
    emp = _scoped_employee(request, pk)
    try:
        credentials = services.reset_password(emp) if emp.user_id else services.create_login(emp)
    except services.EmployeeError as exc:
        messages.error(request, str(exc))
    else:
        request.session["new_login"] = {"employee": emp.pk, **credentials}
    return redirect("web:employee_detail", pk=emp.pk)


@hr_perm("employees.add_employee")
def employee_next_number(request):
    """HTMX: preview of the number the next employee will get. Reserves nothing."""
    raw = request.GET.get("company", "")
    company = companies_for(request.user).filter(pk=raw).first() if raw.isdigit() else None
    worker_type = request.GET.get("worker_type", "")
    if company is None or worker_type not in Employee.WorkerType.values:
        return HttpResponse('<span class="text-body-secondary">Choose a company first.</span>')
    return HttpResponse(format_html(
        '<strong>{}</strong> <span class="text-body-secondary small">'
        'The final number is assigned when you save.</span>', numbering.peek(company, worker_type)))