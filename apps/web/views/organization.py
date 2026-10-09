from django.contrib import messages
from django.db.models import Count, ProtectedError, RestrictedError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.employees.models import Employee
from apps.organization import services
from apps.organization.models import Department, Designation, Grade, Location, Project

from apps.web.access import hr_perm
from apps.web.forms.organization import DepartmentForm, DesignationForm, GradeForm, ProjectForm, SiteForm

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied

from django.http import HttpResponse, JsonResponse


WORKING = [Employee.Status.ACTIVE, Employee.Status.ON_NOTICE]

ORG_TABS = [
    ("organization.view_company", "web:company_list"),
    ("organization.view_department", "web:department_list"),
    ("organization.view_designation", "web:designation_list"),
    ("organization.view_project", "web:project_list"),
    ("organization.view_location", "web:site_list"),
]

def _form_page(request, form, title, back_url, specs=None, submit="Save"):
    """specs: (column width, card title, rows); a row is a field name or a tuple shown side by side."""
    if specs:
        sections = [{"width": w, "title": t,
                     "rows": [[form[n] for n in ((r,) if isinstance(r, str) else r)] for r in rows]}
                    for w, t, rows in specs]
    else:
        sections = [{"width": 6, "title": "Details", "rows": [[f] for f in form]}]
    return render(request, "web/organization/form.html", {
        "form": form, "title": title, "back_url": back_url, "sections": sections, "submit": submit})

def _project_company_scope(request, manageable):
    """Companies shown on the Projects page: those that run projects, plus any that already have some."""
    ids = set(services.project_companies(request.user).values_list("pk", flat=True))
    ids |= set(Project.objects.filter(company__in=manageable).values_list("company_id", flat=True))
    return manageable.filter(pk__in=ids)

PROJECT_SECTIONS = [(7, "Project", ["company", "name", "location", "code"]),
                    (5, "Schedule", [("start_date", "end_date"), "status"])]

SITE_SECTIONS = [(6, "Site or office", [("name", "code"), "is_site"])]
GRADE_SECTIONS = [(6, "Grade", [("name", "code"), "rank"])]
DEPARTMENT_SECTIONS = [(6, "Department", ["company", ("name", "code"), "parent"])]
DESIGNATION_SECTIONS = [(6, "Designation", ["name"])]

@login_required
def organization_home(request):
    for perm, url_name in ORG_TABS:
        if request.user.has_perm(perm):
            return redirect(url_name)
    raise PermissionDenied

def _staff_counts(user, field, objects):
    rows = (Employee.objects.for_user(user).filter(**{f"{field}__in": objects}, status__in=WORKING)
            .order_by().values_list(field).annotate(n=Count("pk")))
    return dict(rows)


def _department(request, pk):
    qs = (Department.objects.filter(company__in=services.manageable_companies(request.user))
          .select_related("company", "parent"))
    return get_object_or_404(qs, pk=pk)

def _company_filter(request, manageable):
    raw = request.GET.get("company", "")
    return manageable.filter(pk=raw).first() if raw.isdigit() else None

# ------------------------------------------------------------------ departments

@hr_perm("organization.view_department")
def department_list(request):
    manageable = services.manageable_companies(request.user)
    selected = _company_filter(request, manageable)
    qs = Department.objects.filter(company__in=manageable)
    if selected:
        qs = qs.filter(company=selected)
    departments = list(qs.select_related("company", "parent").order_by("company__name", "name"))
    counts = _staff_counts(request.user, "department", departments)
    for d in departments:
        d.staff = counts.get(d.pk, 0)
    return render(request, "web/organization/departments.html", {
        "departments": departments, "multi_company": manageable.count() > 1,
        "companies": manageable, "selected": selected})


@hr_perm("organization.add_department")
def department_create(request):
    form = DepartmentForm(request.POST or None, user=request.user)
    if request.method == "POST" and form.is_valid():
        dept = form.save()
        messages.success(request, f"{dept.name} was added.")
        return redirect("web:department_list")
    return _form_page(request, form, "Add department", reverse("web:department_list"), DEPARTMENT_SECTIONS, "Add department")



@hr_perm("organization.change_department")
def department_edit(request, pk):
    dept = _department(request, pk)
    form = DepartmentForm(request.POST or None, instance=dept, user=request.user)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Department updated.")
        return redirect("web:department_list")
    return _form_page(request, form, f"Edit department: {dept.name}", reverse("web:department_list"), DEPARTMENT_SECTIONS, "Save changes")



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
    grades = (Grade.objects.order_by("-rank", "name")
              if request.user.has_perm("organization.view_grade") else [])
    return render(request, "web/organization/designations.html",
                  {"designations": designations, "grades": grades})


@hr_perm("organization.add_designation")
def designation_create(request):
    form = DesignationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        d = form.save()
        messages.success(request, f"{d.name} was added.")
        return redirect("web:designation_list")
    return _form_page(request, form, "Add designation", reverse("web:designation_list"), DESIGNATION_SECTIONS, "Add designation")


@hr_perm("organization.change_designation")
def designation_edit(request, pk):
    d = get_object_or_404(Designation, pk=pk)
    form = DesignationForm(request.POST or None, instance=d)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Designation updated.")
        return redirect("web:designation_list")
    return _form_page(request, form, f"Edit designation: {d.name}", reverse("web:designation_list"), DESIGNATION_SECTIONS, "Save changes")



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

# ------------------------------------------------------------------ projects

def _project(request, pk):
    qs = (Project.objects.filter(company__in=services.manageable_companies(request.user))
          .select_related("company", "location"))
    return get_object_or_404(qs, pk=pk)


@hr_perm("organization.view_project")
def project_list(request):
    manageable = services.manageable_companies(request.user)
    shown = _project_company_scope(request, manageable)
    selected = _company_filter(request, shown)
    qs = Project.objects.filter(company__in=manageable)
    if selected:
        qs = qs.filter(company=selected)
    projects = qs.select_related("company", "location").order_by("company__name", "code")
    return render(request, "web/organization/projects.html", {
        "projects": projects, "multi_company": shown.count() > 1,
        "companies": shown, "selected": selected})


@hr_perm("organization.add_project")
def project_create(request):
    if not services.project_companies(request.user).exists():
        return render(request, "web/organization/no_project_company.html")
    form = ProjectForm(request.POST or None, user=request.user)
    if request.method == "POST" and form.is_valid():
        p = form.save()
        messages.success(request, f"{p.code} was added.")
        return redirect("web:project_list")
    return _form_page(request, form, "Add project", reverse("web:project_list"), PROJECT_SECTIONS, "Add project")


@hr_perm("organization.change_project")
def project_edit(request, pk):
    p = _project(request, pk)
    form = ProjectForm(request.POST or None, instance=p, user=request.user)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Project updated.")
        return redirect("web:project_list")
    return _form_page(request, form, f"Edit project: {p.code}", reverse("web:project_list"), PROJECT_SECTIONS, "Save changes")



@hr_perm("organization.change_project")
@require_POST
def project_toggle(request, pk):
    """Close an active or on-hold project; reopen a closed one."""
    p = _project(request, pk)
    p.status = Project.Status.ACTIVE if p.status == Project.Status.CLOSED else Project.Status.CLOSED
    p.save(update_fields=["status"])
    messages.success(request, f"{p.code} was {'reopened' if p.status == Project.Status.ACTIVE else 'closed'}.")
    return redirect("web:project_list")


@hr_perm("organization.delete_project")
def project_delete(request, pk):
    p = _project(request, pk)
    blocking = services.blockers(p)
    if request.method == "POST" and not blocking:
        code = p.code
        try:
            p.delete()
        except (ProtectedError, RestrictedError):
            messages.error(request, "Something still depends on this project. Close it instead.")
        else:
            messages.success(request, f"{code} was deleted.")
            return redirect("web:project_list")
    return render(request, "web/organization/delete.html", {
        "kind": "project", "name": p.code, "blockers": blocking,
        "active": p.status != Project.Status.CLOSED,
        "can_change": request.user.has_perm("organization.change_project"),
        "toggle_url": reverse("web:project_toggle", args=[p.pk]),
        "back_url": reverse("web:project_list")})


# ------------------------------------------------------------------ sites and offices

@hr_perm("organization.view_location")
def site_list(request):
    kind = request.GET.get("kind", "sites")
    qs = Location.objects.annotate(project_count=Count("project")).order_by("name")
    if kind == "sites":
        qs = qs.filter(is_site=True)
    elif kind == "offices":
        qs = qs.filter(is_site=False)
    else:
        kind = "all"
    return render(request, "web/organization/sites.html", {"locations": qs, "kind": kind})


@hr_perm("organization.add_location")
def site_create(request):
    form = SiteForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        loc = form.save()
        messages.success(request, f"{loc.name} was added.")
        return redirect("web:site_list")
    return _form_page(request, form, "Add site or office", reverse("web:site_list"), SITE_SECTIONS, "Add")


@hr_perm("organization.change_location")
def site_edit(request, pk):
    loc = get_object_or_404(Location, pk=pk)
    form = SiteForm(request.POST or None, instance=loc)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Updated.")
        return redirect("web:site_list")
    return _form_page(request, form, f"Edit: {loc.name}", reverse("web:site_list"), SITE_SECTIONS)


@hr_perm("organization.change_location")
@require_POST
def site_toggle(request, pk):
    loc = get_object_or_404(Location, pk=pk)
    loc.is_active = not loc.is_active
    loc.save(update_fields=["is_active"])
    messages.success(request, f"{loc.name} was {'reactivated' if loc.is_active else 'deactivated'}.")
    return redirect("web:site_list")


@hr_perm("organization.delete_location")
def site_delete(request, pk):
    loc = get_object_or_404(Location, pk=pk)
    blocking = services.blockers(loc)
    if request.method == "POST" and not blocking:
        name = loc.name
        try:
            loc.delete()
        except (ProtectedError, RestrictedError):
            messages.error(request, "Something still depends on this. Deactivate it instead.")
        else:
            messages.success(request, f"{name} was deleted.")
            return redirect("web:site_list")
    return render(request, "web/organization/delete.html", {
        "kind": "site", "name": loc.name, "blockers": blocking, "active": loc.is_active,
        "can_change": request.user.has_perm("organization.change_location"),
        "toggle_url": reverse("web:site_toggle", args=[loc.pk]),
        "back_url": reverse("web:site_list")})


# ------------------------------------------------------------------ grades (shown on the Designations tab)

@hr_perm("organization.add_grade")
def grade_create(request):
    form = GradeForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        g = form.save()
        messages.success(request, f"{g.name} was added.")
        return redirect("web:designation_list")
    return _form_page(request, form, "Add grade", reverse("web:designation_list"), GRADE_SECTIONS, "Add grade")


@hr_perm("organization.change_grade")
def grade_edit(request, pk):
    g = get_object_or_404(Grade, pk=pk)
    form = GradeForm(request.POST or None, instance=g)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Grade updated.")
        return redirect("web:designation_list")
    return _form_page(request, form, f"Edit grade: {g.name}", reverse("web:designation_list"), GRADE_SECTIONS, "Save changes")


@hr_perm("organization.delete_grade")
def grade_delete(request, pk):
    g = get_object_or_404(Grade, pk=pk)
    blocking = services.blockers(g)
    if request.method == "POST" and not blocking:
        name = g.name
        try:
            g.delete()
        except (ProtectedError, RestrictedError):
            messages.error(request, "Something still depends on this grade.")
        else:
            messages.success(request, f"{name} was deleted.")
            return redirect("web:designation_list")
    return render(request, "web/organization/delete.html", {
        "kind": "grade", "name": g.name, "blockers": blocking, "active": False,
        "can_change": False, "toggle_url": "",
        "back_url": reverse("web:designation_list")})

@hr_perm("organization.add_location")
def site_code_preview(request):
    """The code a new site would get from its name; the add form fills the Code box with it."""
    name = " ".join(request.GET.get("name", "").split())
    return HttpResponse(services.suggest_site_code(Location.objects.all(), name) if name else "")

@login_required
@require_POST
def company_projects_set(request, pk):
    """HTMX: switch 'runs projects' on or off for one company (Settings > Organization)."""
    if not request.user.has_perm("configuration.edit_settings_organization"):
        raise PermissionDenied
    company = get_object_or_404(services.companies_for(request.user), pk=pk)
    on = request.POST.get(f"runs-{company.pk}") is not None
    if company.runs_projects != on:
        company.runs_projects = on
        company.save(update_fields=["runs_projects"])
    return HttpResponse('<span class="badge text-bg-success">Runs projects</span>' if on
                        else '<span class="badge text-bg-secondary">No projects</span>')


@login_required
def site_search(request):
    """JSON for the project form's site box: active sites matching what was typed, best matches first."""
    if not (request.user.has_perm("organization.add_project") or request.user.has_perm("organization.change_project")):
        raise PermissionDenied
    q = " ".join(request.GET.get("q", "").split())
    if not q:
        return JsonResponse({"results": []})
    rows = list(Location.objects.filter(is_site=True, is_active=True, name__icontains=q).order_by("name")[:30])
    rows.sort(key=lambda s: (not s.name.lower().startswith(q.lower()), s.name.lower()))
    return JsonResponse({"results": [{"id": s.pk, "name": s.name, "code": s.code} for s in rows[:8]]})


@hr_perm("organization.add_project")
def project_code_preview(request):
    """The code a new project would get from its name and site; the add form fills the Code box with it."""
    name = " ".join(request.GET.get("name", "").split())
    if not name:
        return HttpResponse("")
    company_id, site_id = request.GET.get("company", ""), request.GET.get("location", "")
    company = services.project_companies(request.user).filter(pk=company_id).first() if company_id.isdigit() else None
    site = Location.objects.filter(pk=site_id, is_site=True, is_active=True).first() if site_id.isdigit() else None
    return HttpResponse(services.suggest_project_code(company, name, site))
