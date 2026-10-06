from django.contrib import messages
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.employees.models import Employee
from apps.organization import services

from .company_forms import CompanyForm
from .hr_views import hr_perm


def _company(request, pk):
    """Only companies this user may manage; anything else is a 404."""
    return get_object_or_404(services.manageable_companies(request.user), pk=pk)


@hr_perm("organization.view_company")
def company_list(request):
    working = [Employee.Status.ACTIVE, Employee.Status.ON_NOTICE]
    companies = (services.manageable_companies(request.user)
                 .annotate(staff=Count("employees", filter=Q(employees__status__in=working)))
                 .order_by("-is_active", "name"))
    return render(request, "web/company_list.html", {"companies": companies})


@hr_perm("organization.add_company")
def company_create(request):
    form = CompanyForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        company = services.create_company(request.user, form.save(commit=False))
        messages.success(request, f"{company.name} was added.")
        return redirect("web:company_list")
    return render(request, "web/form_page.html", {
        "form": form, "title": "Add company", "back_url": reverse("web:company_list")})


@hr_perm("organization.change_company")
def company_edit(request, pk):
    company = _company(request, pk)
    form = CompanyForm(request.POST or None, instance=company)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Company details updated.")
        return redirect("web:company_list")
    return render(request, "web/form_page.html", {
        "form": form, "title": f"Edit company: {company.name}", "back_url": reverse("web:company_list")})


@hr_perm("organization.change_company")
@require_POST
def company_toggle(request, pk):
    company = _company(request, pk)
    activate = not company.is_active
    try:
        services.set_active(company, activate)
    except services.CompanyError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f"{company.name} was {'reactivated' if activate else 'deactivated'}.")
    return redirect("web:company_list")


@hr_perm("organization.delete_company")
def company_delete(request, pk):
    company = _company(request, pk)
    blocking = services.blockers(company)
    error = ""
    if request.method == "POST" and not blocking:
        if request.POST.get("confirm_code", "").strip().upper() == company.code.upper():
            name = company.name
            try:
                services.delete_company(company)
            except services.CompanyError as exc:
                error = str(exc)
            else:
                messages.success(request, f"{name} was deleted.")
                return redirect("web:company_list")
        else:
            error = "That does not match the company code. Nothing was deleted."
    return render(request, "web/company_delete.html",
                  {"company": company, "blockers": blocking, "error": error})