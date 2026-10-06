from django.contrib import messages
from django.db.models import Count, ProtectedError, RestrictedError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.employees.models import Employee
from apps.organization import services
from apps.organization.models import Department, Designation

from apps.web.access import hr_perm
from apps.web.forms.organization import DepartmentForm, DesignationForm

WORKING = [Employee.Status.ACTIVE, Employee.Status.ON_NOTICE]


def _staff_counts(user, field, objects):
    rows = (Employee.objects.for_user(user).filter(**{f"{field}__in": objects}, status__in=WORKING)
            .order_by().values_list(field).annotate(n=Count("pk")))
    return dict(rows)


def _department(request, pk):
    qs = (Department.objects.filter(company__in=services.manageable_companies(request.user))
          .select_related("company", "parent"))
    return get_object_or_404(qs, pk=pk)


# ------------------------------------------------------------------ departments

@hr_perm("organization.view_department")
def department_list(request):
    manageable = services.manageable_companies(request.user)
    departments = list(Department.objects.filter(company__in=manageable)
                       .select_related("company", "parent").order_by("company__name", "name"))
    counts = _staff_counts(request.user, "department", departments)
    for d in departments:
        d.staff = counts.get(d.pk, 0)
    return render(request, "web/organization/departments.html",
                  {"departments": departments, "multi_company": manageable.count() > 1})


@hr_perm("organization.add_department")
def department_create(request):
    form = DepartmentForm(request.POST or None, user=request.user)
    if request.method == "POST" and form.is_valid():
        dept = form.save()
        messages.success(request, f"{dept.name} was added.")
        return redirect("web:department_list")
    return render(request, "web/form_page.html", {
        "form": form, "title": "Add department", "back_url": reverse("web:department_list")})


@hr_perm("organization.change_department")
def department_edit(request, pk):
    dept = _department(request, pk)
    form = DepartmentForm(request.POST or None, instance=dept, user=request.user)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Department updated.")
        return redirect("web:department_list")
    return render(request, "web/form_page.html", {
        "form": form, "title": f"Edit department: {dept.name}", "back_url": reverse("web:department_list")})


@hr_perm("organization.change_department")
@require_POST
def department_toggle(request, pk):
    dept = _department(request, pk)
    dept.is_active = not dept.is_active
    dept.save(update_fields=["is_active"])
    messages.success(request, f"{dept.name} was {'reactivated' if dept.is_active else 'deactivated'}.")
    return redirect("web:department_list")


@hr_perm("organization.delete_department")
def department_delete(request, pk):
    dept = _department(request, pk)
    blocking = services.blockers(dept)
    if request.method == "POST" and not blocking:
        name = dept.name
        try:
            dept.delete()
        except (ProtectedError, RestrictedError):
            messages.error(request, "Something still depends on this department. Deactivate it instead.")
        else:
            messages.success(request, f"{name} was deleted.")
            return redirect("web:department_list")
    return render(request, "web/organization/delete.html", {
        "kind": "department", "name": dept.name, "blockers": blocking, "active": dept.is_active,
        "can_change": request.user.has_perm("organization.change_department"),
        "toggle_url": reverse("web:department_toggle", args=[dept.pk]),
        "back_url": reverse("web:department_list")})


# ------------------------------------------------------------------ designations

@hr_perm("organization.view_designation")
def designation_list(request):
    designations = list(Designation.objects.order_by("name"))
    counts = _staff_counts(request.user, "designation", designations)
    for d in designations:
        d.staff = counts.get(d.pk, 0)
    return render(request, "web/organization/designations.html", {"designations": designations})


@hr_perm("organization.add_designation")
def designation_create(request):
    form = DesignationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        d = form.save()
        messages.success(request, f"{d.name} was added.")
        return redirect("web:designation_list")
    return render(request, "web/form_page.html", {
        "form": form, "title": "Add designation", "back_url": reverse("web:designation_list")})


@hr_perm("organization.change_designation")
def designation_edit(request, pk):
    d = get_object_or_404(Designation, pk=pk)
    form = DesignationForm(request.POST or None, instance=d)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Designation updated.")
        return redirect("web:designation_list")
    return render(request, "web/form_page.html", {
        "form": form, "title": f"Edit designation: {d.name}", "back_url": reverse("web:designation_list")})


@hr_perm("organization.change_designation")
@require_POST
def designation_toggle(request, pk):
    d = get_object_or_404(Designation, pk=pk)
    d.is_active = not d.is_active
    d.save(update_fields=["is_active"])
    messages.success(request, f"{d.name} was {'reactivated' if d.is_active else 'deactivated'}.")
    return redirect("web:designation_list")


@hr_perm("organization.delete_designation")
def designation_delete(request, pk):
    d = get_object_or_404(Designation, pk=pk)
    blocking = services.blockers(d)
    if request.method == "POST" and not blocking:
        name = d.name
        try:
            d.delete()
        except (ProtectedError, RestrictedError):
            messages.error(request, "Something still depends on this designation. Deactivate it instead.")
        else:
            messages.success(request, f"{name} was deleted.")
            return redirect("web:designation_list")
    return render(request, "web/organization/delete.html", {
        "kind": "designation", "name": d.name, "blockers": blocking, "active": d.is_active,
        "can_change": request.user.has_perm("organization.change_designation"),
        "toggle_url": reverse("web:designation_toggle", args=[d.pk]),
        "back_url": reverse("web:designation_list")})