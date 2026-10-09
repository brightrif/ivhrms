from django.contrib import messages
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.employees.models import Employee
from apps.labor import deployment
from apps.labor.allocation import LaborAllocation, WorkOrder
from apps.labor.models import LaborProfile
from apps.labor.services import LaborError
from apps.organization.models import Location, Project
from apps.organization.services import companies_for

from apps.web.access import hr_perm
from apps.web.forms.labor_sites import AllocationForm, ReleaseForm, TransferForm, WorkOrderForm

_PLACE_ROWS = [["project", "location"], ["work_order"]]
LAYOUTS = {
    "allocate": [{"title": "Site", "width": "col-lg-7", "rows": _PLACE_ROWS + [["effective_from"], ["notes"]]}],
    "transfer": [{"title": "Destination", "width": "col-lg-7", "rows": _PLACE_ROWS + [["effective_from"]]}],
    "release": [{"title": "Release from site", "width": "col-lg-5", "rows": [["last_day"]]}],
    "work_order": [{"title": "Work order", "width": "col-lg-7",
                    "rows": [["project"], ["code", "name"], ["start_date", "end_date"]]}],
}
# fields that are hidden inputs still have to be "placed" so the layout test can see every field is accounted for
LAYOUTS["transfer"][0]["rows"].append(["profiles"])


def _form_page(request, form, title, back_url, layout, intro="", submit_label="Save"):
    return render(request, "web/form_layout.html", {
        "form": form, "title": title, "back_url": back_url, "layout": LAYOUTS[layout], "intro": intro,
        "submit_label": submit_label})


def _profile(request, pk):
    qs = LaborProfile.objects.for_user(request.user).select_related("employee", "company", "trade")
    return get_object_or_404(qs, pk=pk)


def _site_url(project, location):
    return reverse("web:labor_site_detail", args=[project.pk, location.pk])


# ------------------------------------------------------------------ sites

@hr_perm("labor.view_laborallocation")
def site_list(request):
    rows = deployment.site_overview(request.user)
    return render(request, "web/labor/sites.html", {
        "rows": rows, "totals": deployment.headcount(request.user),
        "unallocated": deployment.unallocated_profiles(request.user).count()})


@hr_perm("labor.view_laborallocation")
def site_detail(request, project_pk, location_pk):
    project = get_object_or_404(Project.objects.filter(company__in=companies_for(request.user))
                                .select_related("company"), pk=project_pk)
    location = get_object_or_404(Location, pk=location_pk)
    allocations = list(LaborAllocation.objects.for_user(request.user)
                       .filter(project=project, location=location, effective_to__isnull=True)
                       .exclude(employee__status=Employee.Status.SEPARATED)
                       .select_related("employee", "employee__labor_profile__trade", "employee__labor_profile__contractor",
                                       "work_order").order_by("employee__employee_no"))
    return render(request, "web/labor/site_detail.html", {
        "project": project, "location": location, "allocations": allocations,
        "direct": sum(1 for a in allocations if a.employee.labor_profile.engagement == "direct"),
        "contracted": sum(1 for a in allocations if a.employee.labor_profile.engagement == "contracted")})


# ------------------------------------------------------------------ allocating

@hr_perm("labor.add_laborallocation")
def labor_allocate(request, pk):
    profile = _profile(request, pk)
    back = reverse("web:labor_detail", args=[profile.pk])
    form = AllocationForm(request.POST or None, employee=profile.employee, user=request.user)
    if request.method == "POST" and form.is_valid():
        cd = form.cleaned_data
        try:
            deployment.allocate(profile.employee, project=cd["project"], location=cd["location"],
                                work_order=cd["work_order"], effective_from=cd["effective_from"], notes=cd["notes"])
        except LaborError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, f"{profile.employee.full_name} is now on {cd['project'].code}, {cd['location'].name}.")
            return redirect(back)
    mine = deployment.open_allocations(profile.employee)
    if len(mine) > 1:
        intro = (f"On {len(mine)} projects: {', '.join(a.project.code for a in mine)}. "
                 "To change them, use each project's Team page.")
    elif mine:
        intro = f"Now at {mine[0].place}, since {mine[0].effective_from:%d %b %Y}."
    else:
        intro = "Not on any site yet."
    return _form_page(request, form, f"Allocate: {profile.employee.full_name}", back, "allocate", intro=intro)


@hr_perm("labor.change_laborallocation")
def labor_release(request, pk):
    profile = _profile(request, pk)
    back = reverse("web:labor_detail", args=[profile.pk])
    current = deployment.open_allocation(profile.employee)
    if current is None:
        messages.error(request, "This worker is not allocated anywhere.")
        return redirect(back)
    form = ReleaseForm(request.POST or None, allocation=current)
    if request.method == "POST" and form.is_valid():
        try:
            deployment.release(profile.employee, last_day=form.cleaned_data["last_day"])
        except LaborError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, f"{profile.employee.full_name} was taken off the site.")
            return redirect(back)
    return _form_page(request, form, f"Release: {profile.employee.full_name}", back, "release",
                      intro=f"Now at {current.place}, since {current.effective_from:%d %b %Y}.",
                      submit_label="Release")


@hr_perm("labor.add_laborallocation")
def labor_transfer(request):
    """Allocate or move several workers at once. The list page and the site page send the ticked workers here."""
    if request.method == "POST":
        form = TransferForm(request.POST, user=request.user)
        ids = request.POST.getlist("profiles")
    else:
        ids = [i for i in request.GET.getlist("profiles") if i.isdigit()]
        if not ids:
            messages.error(request, "Tick at least one worker first.")
            return redirect("web:labor_list")
        form = TransferForm(user=request.user, initial={"profiles": ids})
    people = list(LaborProfile.objects.for_user(request.user).filter(pk__in=[i for i in ids if str(i).isdigit()])
                  .select_related("employee").order_by("employee__employee_no"))
    if request.method == "POST" and form.is_valid():
        cd = form.cleaned_data
        try:
            moved, already = deployment.transfer([p.employee for p in cd["profiles"]], project=cd["project"],
                                                 location=cd["location"], work_order=cd["work_order"],
                                                 effective_from=cd["effective_from"])
        except LaborError as exc:
            form.add_error(None, str(exc))
        else:
            note = f" {already} {'was' if already == 1 else 'were'} already there." if already else ""
            messages.success(request, f"{moved} worker{'s' if moved != 1 else ''} moved to "
                                      f"{cd['project'].code}, {cd['location'].name}.{note}")
            return redirect(_site_url(cd["project"], cd["location"]))
    names = ", ".join(p.employee.full_name for p in people[:8]) + (f" and {len(people) - 8} more" if len(people) > 8 else "")
    return _form_page(request, form, "Allocate / move workers", reverse("web:labor_list"), "transfer",
                      intro=f"{len(people)} worker{'s' if len(people) != 1 else ''}: {names}", submit_label="Move")


# ------------------------------------------------------------------ work orders

@hr_perm("labor.view_workorder")
def work_order_list(request):
    companies = companies_for(request.user)
    orders = (WorkOrder.objects.for_user(request.user).select_related("project", "company")
              .annotate(worker_count=Count("allocations", filter=Q(allocations__effective_to__isnull=True)
                                           & ~Q(allocations__employee__status=Employee.Status.SEPARATED)))
              .order_by("status", "project__code", "code"))
    return render(request, "web/labor/work_orders.html", {"orders": orders, "multi_company": companies.count() > 1})


@hr_perm("labor.add_workorder")
def work_order_create(request):
    form = WorkOrderForm(request.POST or None, user=request.user)
    if request.method == "POST" and form.is_valid():
        w = form.save()
        messages.success(request, f"Work order {w.code} was added.")
        return redirect("web:labor_work_order_list")
    return _form_page(request, form, "Add work order", reverse("web:labor_work_order_list"), "work_order")


@hr_perm("labor.change_workorder")
def work_order_edit(request, pk):
    order = get_object_or_404(WorkOrder.objects.for_user(request.user).select_related("project"), pk=pk)
    form = WorkOrderForm(request.POST or None, instance=order, user=request.user)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Work order updated.")
        return redirect("web:labor_work_order_list")
    return _form_page(request, form, f"Edit work order: {order.code}", reverse("web:labor_work_order_list"), "work_order")


@hr_perm("labor.change_workorder")
@require_POST
def work_order_toggle(request, pk):
    order = get_object_or_404(WorkOrder.objects.for_user(request.user), pk=pk)
    reopening = order.status == WorkOrder.Status.CLOSED
    try:
        deployment.set_work_order_open(order, reopening)
    except LaborError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f"Work order {order.code} was {'reopened' if reopening else 'closed'}.")
    return redirect("web:labor_work_order_list")
