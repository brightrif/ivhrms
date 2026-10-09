"""Putting workers on projects. Adding is the only thing that happens here: it never ends another project.
Taking a worker off a project is a separate, per-project action with its own last day."""
from datetime import date

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.employees.models import Employee
from apps.labor import deployment
from apps.labor.allocation import LaborAllocation
from apps.labor.models import LaborProfile
from apps.labor.services import LaborError

from apps.web.access import hr_perm
from apps.web.forms.labor_projects import AddCrewForm, AddProjectForm

LAYOUTS = {
    "add_project": [{"title": "Add to a project", "width": "col-lg-6", "rows": [["project"], ["effective_from"], ["work_order"]]}],
    "add_crew": [{"title": "Add to a project", "width": "col-lg-6", "rows": [["project"], ["effective_from"], ["profiles"]]}],
}


def _form_page(request, form, title, back_url, layout, intro="", submit_label="Save"):
    return render(request, "web/form_layout.html", {
        "form": form, "title": title, "back_url": back_url, "layout": LAYOUTS[layout], "intro": intro,
        "submit_label": submit_label})


def _profile(request, pk):
    qs = LaborProfile.objects.for_user(request.user).select_related("employee", "company", "trade")
    return get_object_or_404(qs, pk=pk)


def _day(raw):
    try:
        return date.fromisoformat(raw or "")
    except ValueError:
        return None


@hr_perm("labor.add_laborallocation")
def labor_project_add(request, pk):
    profile = _profile(request, pk)
    employee = profile.employee
    back = reverse("web:labor_detail", args=[profile.pk])
    if employee.status == Employee.Status.SEPARATED:
        messages.error(request, "This worker has left the company.")
        return redirect(back)
    form = AddProjectForm(request.POST or None, employee=employee, user=request.user)
    if request.method == "POST" and form.is_valid():
        cd = form.cleaned_data
        try:
            deployment.join_project(employee, project=cd["project"], work_order=cd["work_order"],
                                    effective_from=cd["effective_from"])
        except LaborError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, f"{employee.full_name} is now also on {cd['project'].code}.")
            return redirect(back)
    mine = deployment.open_allocations(employee)
    intro = ("Now on: " + ", ".join(f"{a.project.code} {a.project.name}" for a in mine) + "." if mine
             else "Not on any project yet.")
    if profile.serves_all_projects:
        intro = "Works for every active project already. " + intro
    if form.without_site:
        intro += (" Not listed because they have no site yet: " + ", ".join(p.code for p in form.without_site)
                  + ". Set the site under Organization, Projects.")
    return _form_page(request, form, f"Add to a project: {employee.full_name}", back, "add_project", intro=intro,
                      submit_label="Add to project")


@hr_perm("labor.change_laborallocation")
@require_POST
def labor_project_remove(request, pk):
    allocation = get_object_or_404(LaborAllocation.objects.for_user(request.user).select_related("employee", "project"),
                                   pk=pk, effective_to__isnull=True)
    profile = allocation.employee.labor_profile
    back = reverse("web:labor_detail", args=[profile.pk])
    last = _day(request.POST.get("last_day"))
    if last is None:
        messages.error(request, "Enter the last day on this project.")
        return redirect(back)
    try:
        deployment.leave_project(allocation, last_day=last)
    except LaborError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f"{allocation.employee.full_name} left {allocation.project.code} (last day {last:%d %b %Y}).")
    return redirect(back)


@hr_perm("labor.add_laborallocation")
def labor_projects_add_many(request):
    """Several workers, one more project each. The list page and the site page send the ticked workers here."""
    if request.method == "POST":
        form = AddCrewForm(request.POST, user=request.user)
        ids = request.POST.getlist("profiles")
    else:
        ids = [i for i in request.GET.getlist("profiles") if i.isdigit()]
        if not ids:
            messages.error(request, "Tick at least one worker first.")
            return redirect("web:labor_list")
        form = AddCrewForm(user=request.user, initial={"profiles": ids})
    people = list(LaborProfile.objects.for_user(request.user).filter(pk__in=[i for i in ids if str(i).isdigit()])
                  .select_related("employee").order_by("employee__employee_no"))
    if request.method == "POST" and form.is_valid():
        cd = form.cleaned_data
        try:
            added, already = deployment.add_workers([p.employee for p in cd["profiles"]], project=cd["project"],
                                                    effective_from=cd["effective_from"])
        except LaborError as exc:
            form.add_error(None, str(exc))
        else:
            note = f" {already} {'was' if already == 1 else 'were'} already on it." if already else ""
            messages.success(request, f"{added} worker{'s' if added != 1 else ''} added to {cd['project'].code}.{note}")
            return redirect("web:labor_team", project_pk=cd["project"].pk)
    names = ", ".join(p.employee.full_name for p in people[:8]) + (f" and {len(people) - 8} more" if len(people) > 8 else "")
    return _form_page(request, form, "Add workers to a project", reverse("web:labor_list"), "add_crew",
                      intro=f"{len(people)} worker{'s' if len(people) != 1 else ''}: {names}. Their other projects stay as they are.",
                      submit_label="Add to project")


@hr_perm("labor.add_laborprofile")
@require_POST
def labor_shared_set(request, pk):
    profile = _profile(request, pk)
    shared = request.POST.get("shared") is not None
    deployment.set_shared(profile, shared)
    messages.success(request, f"{profile.employee.full_name} "
                              f"{'now works for every project' if shared else 'is no longer shared between all projects'}.")
    return redirect("web:labor_detail", pk=profile.pk)
