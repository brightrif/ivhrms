from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, Prefetch, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.employees.models import Employee
from apps.labor import deployment, services
from apps.labor.overtime_services import overtime_mode
from apps.labor.allocation import LaborAllocation
from apps.labor.models import Contractor, LaborProfile, Trade
from apps.organization.models import Project
from apps.organization.services import companies_for

from apps.web.access import hr_perm
from apps.web.forms.labor import (
    ContractorForm,
    LaborWorkerForm,
    ProfileEditForm,
    ProfileSetupForm,
    RateForm,
    TradeForm,
)

# Full-page layouts, side by side on a laptop screen so nothing needs scrolling. See form_layout.html.
_ENGAGEMENT = {"title": "Engagement", "width": "col-lg-4",
               "rows": [["engagement"], ["contractor"], ["trade"], ["notes"]]}
LAYOUTS = {
    "worker": [
        {"title": "Worker", "width": "col-lg-4",
         "rows": [["company"], ["first_name", "last_name"], ["nationality", "phone"], ["joining_date"]]},
        _ENGAGEMENT,
        {"title": "Pay", "width": "col-lg-4",
         "rows": [["wage_basis", "rate"], ["standard_hours"], ["overtime_eligible"]]},
    ],
    "setup": [
        _ENGAGEMENT,
        {"title": "Pay", "width": "col-lg-4",
         "rows": [["effective_from"], ["wage_basis", "rate"], ["standard_hours"], ["overtime_eligible"]]},
    ],
    "edit": [{"title": "Engagement", "width": "col-lg-5",
              "rows": [["engagement"], ["contractor"], ["trade"], ["notes"]]}],
    "rate": [{"title": "New pay terms", "width": "col-lg-6",
              "rows": [["effective_from", "wage_basis"], ["rate", "standard_hours"], ["overtime_eligible"]]}],
    "contractor": [
        {"title": "Contractor", "width": "col-lg-7",
         "rows": [["company", "name"], ["name_ar", "cr_number"], ["contact_person", "phone", "email"]]},
        {"title": "More", "width": "col-lg-5", "rows": [["address"], ["notes"]]},
    ],
    "trade": [{"title": "Trade", "width": "col-lg-7", "rows": [["code", "name", "name_ar"]]}],
}


def _form_page(request, form, title, back_url, layout, intro=""):
    return render(request, "web/form_layout.html", {
        "form": form, "title": title, "back_url": back_url, "layout": LAYOUTS[layout], "intro": intro})


def _profile(request, pk):
    qs = LaborProfile.objects.for_user(request.user).select_related("employee", "company", "trade", "contractor")
    return get_object_or_404(qs, pk=pk)


def _contractor(request, pk):
    return get_object_or_404(Contractor.objects.for_user(request.user).select_related("company"), pk=pk)


# ------------------------------------------------------------------ workers

@hr_perm("labor.view_laborprofile")
def labor_list(request):
    q = request.GET.get("q", "").strip()
    company, engagement, trade, status, project = (
        request.GET.get(k, "") for k in ("company", "engagement", "trade", "status", "project"))
    companies = companies_for(request.user)
    open_allocations = LaborAllocation.objects.filter(effective_to__isnull=True).select_related(
        "project", "location", "work_order")
    qs = (LaborProfile.objects.for_user(request.user)
          .select_related("employee", "company", "trade", "contractor")
          .prefetch_related("rates", Prefetch("employee__labor_allocations", queryset=open_allocations,
                                              to_attr="open_allocs")))
    if q:
        qs = qs.filter(Q(employee__employee_no__icontains=q) | Q(employee__first_name__icontains=q)
                       | Q(employee__last_name__icontains=q) | Q(employee__phone__icontains=q)
                       | Q(contractor__name__icontains=q))
    if company.isdigit():
        qs = qs.filter(company_id=company)
    if engagement in LaborProfile.Engagement.values:
        qs = qs.filter(engagement=engagement)
    if trade.isdigit():
        qs = qs.filter(trade_id=trade)
    if project == "none":                                        # workers who are not on any site
        qs = qs.exclude(employee_id__in=LaborAllocation.objects.filter(effective_to__isnull=True).values("employee_id"))
    elif project.isdigit():
        qs = qs.filter(employee__labor_allocations__project_id=project,
                       employee__labor_allocations__effective_to__isnull=True)
    if status in Employee.Status.values:
        qs = qs.filter(employee__status=status)
    else:
        qs = qs.exclude(employee__status=Employee.Status.SEPARATED)      # people who left show only when asked for
    params = request.GET.copy()
    params.pop("page", None)
    unassigned = list(services.unassigned_workers(request.user)[:8])
    return render(request, "web/labor/list.html", {
        "page": Paginator(qs, 50).get_page(request.GET.get("page")), "extra": params.urlencode(),
        "q": q, "company": company, "engagement": engagement, "trade": trade, "status": status, "project": project,
        "projects": Project.objects.filter(company__in=companies, status=Project.Status.ACTIVE).order_by("code"),
        "can_allocate": request.user.has_perm("labor.add_laborallocation"),
        "companies": companies, "multi_company": companies.count() > 1,
        "engagements": LaborProfile.Engagement.choices, "statuses": Employee.Status.choices,
        "trades": Trade.objects.order_by("name"), "show_rates": request.user.has_perm("labor.view_laborrate"),
        "unassigned": unassigned, "unassigned_total": services.unassigned_workers(request.user).count()})


@hr_perm("labor.view_laborprofile")
def labor_detail(request, pk):
    profile = _profile(request, pk)
    allocations = list(LaborAllocation.objects.filter(employee=profile.employee)
                       .select_related("project", "location", "work_order"))
    return render(request, "web/labor/detail.html", {
        "profile": profile, "emp": profile.employee, "rates": list(profile.rates.all()),
        "allocations": allocations, "current_allocation": next((a for a in allocations if a.is_open), None),
        "overtime_mode": overtime_mode(profile.company, timezone.localdate())[0],
        "show_rates": request.user.has_perm("labor.view_laborrate")})


@hr_perm("employees.add_employee")
@hr_perm("labor.add_laborprofile")
def labor_create(request):
    form = LaborWorkerForm(request.POST or None, user=request.user)
    if request.method == "POST" and form.is_valid():
        cd = form.cleaned_data
        employee = Employee(company=cd["company"], first_name=cd["first_name"], last_name=cd["last_name"],
                            nationality=cd["nationality"], phone=cd["phone"], joining_date=cd["joining_date"])
        try:
            profile = services.create_labor_worker(
                employee, engagement=cd["engagement"], contractor=cd["contractor"], trade=cd["trade"],
                notes=cd["notes"], wage_basis=cd["wage_basis"], rate=cd["rate"],
                standard_hours=cd["standard_hours"], overtime_eligible=cd["overtime_eligible"])
        except services.LaborError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, f"{employee.full_name} was added as {profile.employee.employee_no}.")
            return redirect("web:labor_detail", pk=profile.pk)
    return _form_page(request, form, "Add labor worker", reverse("web:labor_list"), "worker",
                      intro="The worker gets an employee number automatically.")


@hr_perm("labor.add_laborprofile")
def labor_setup(request, employee_pk):
    employee = get_object_or_404(Employee.objects.for_user(request.user).select_related("company"), pk=employee_pk,
                                 worker_type=Employee.WorkerType.LABOR)
    if hasattr(employee, "labor_profile"):
        return redirect("web:labor_detail", pk=employee.labor_profile.pk)
    form = ProfileSetupForm(request.POST or None, employee=employee, user=request.user)
    if request.method == "POST" and form.is_valid():
        cd = form.cleaned_data
        try:
            profile = services.setup_profile(
                employee, engagement=cd["engagement"], contractor=cd["contractor"], trade=cd["trade"],
                notes=cd["notes"], wage_basis=cd["wage_basis"], rate=cd["rate"],
                standard_hours=cd["standard_hours"], overtime_eligible=cd["overtime_eligible"],
                effective_from=cd["effective_from"])
        except services.LaborError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, f"Labor profile added for {employee.full_name}.")
            return redirect("web:labor_detail", pk=profile.pk)
    return _form_page(request, form, f"Labor profile: {employee.full_name}", reverse("web:labor_list"), "setup",
                      intro=f"{employee.employee_no} is a labor worker without a profile yet.")


@hr_perm("labor.change_laborprofile")
def labor_edit(request, pk):
    profile = _profile(request, pk)
    back = reverse("web:labor_detail", args=[profile.pk])
    form = ProfileEditForm(request.POST or None, profile=profile, user=request.user)
    if request.method == "POST" and form.is_valid():
        cd = form.cleaned_data
        try:
            services.update_profile(profile, engagement=cd["engagement"], contractor=cd["contractor"],
                                    trade=cd["trade"], notes=cd["notes"])
        except services.LaborError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "Details updated.")
            return redirect(back)
    return _form_page(request, form, f"Edit: {profile.employee.full_name}", back, "edit")


@hr_perm("labor.add_laborrate")
def labor_rate_change(request, pk):
    profile = _profile(request, pk)
    back = reverse("web:labor_detail", args=[profile.pk])
    form = RateForm(request.POST or None, profile=profile)
    if request.method == "POST" and form.is_valid():
        cd = form.cleaned_data
        try:
            services.change_rate(profile, effective_from=cd["effective_from"], wage_basis=cd["wage_basis"],
                                 rate=cd["rate"], standard_hours=cd["standard_hours"],
                                 overtime_eligible=cd["overtime_eligible"])
        except services.LaborError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "New pay terms saved. The earlier terms are kept in the history.")
            return redirect(back)
    return _form_page(request, form, f"Change pay: {profile.employee.full_name}", back, "rate",
                      intro="The current terms end the day before the new ones start.")


# ------------------------------------------------------------------ contractors

@hr_perm("labor.view_contractor")
def contractor_list(request):
    companies = companies_for(request.user)
    contractors = (Contractor.objects.for_user(request.user).select_related("company")
                   .annotate(worker_count=Count("workers", filter=~Q(workers__employee__status=Employee.Status.SEPARATED)))
                   .order_by("-is_active", "name"))
    return render(request, "web/labor/contractors.html", {
        "contractors": contractors, "multi_company": companies.count() > 1})


@hr_perm("labor.add_contractor")
def contractor_create(request):
    form = ContractorForm(request.POST or None, user=request.user)
    if request.method == "POST" and form.is_valid():
        c = form.save()
        messages.success(request, f"{c.name} was added.")
        return redirect("web:labor_contractor_list")
    return _form_page(request, form, "Add contractor", reverse("web:labor_contractor_list"), "contractor")


@hr_perm("labor.change_contractor")
def contractor_edit(request, pk):
    contractor = _contractor(request, pk)
    form = ContractorForm(request.POST or None, instance=contractor, user=request.user)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Contractor updated.")
        return redirect("web:labor_contractor_list")
    return _form_page(request, form, f"Edit contractor: {contractor.name}",
                      reverse("web:labor_contractor_list"), "contractor")


@hr_perm("labor.change_contractor")
@require_POST
def contractor_toggle(request, pk):
    contractor = _contractor(request, pk)
    try:
        services.set_contractor_active(contractor, not contractor.is_active)
    except services.LaborError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f"{contractor.name} was {'deactivated' if contractor.is_active else 'reactivated'}.")
    return redirect("web:labor_contractor_list")


# ------------------------------------------------------------------ trades

@hr_perm("labor.view_trade")
def trade_list(request):
    trades = Trade.objects.annotate(
        worker_count=Count("workers", filter=~Q(workers__employee__status=Employee.Status.SEPARATED))
    ).order_by("-is_active", "name")
    return render(request, "web/labor/trades.html", {"trades": trades})


@hr_perm("labor.add_trade")
def trade_create(request):
    form = TradeForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        t = form.save()
        messages.success(request, f"{t.name} was added.")
        return redirect("web:labor_trade_list")
    return _form_page(request, form, "Add trade", reverse("web:labor_trade_list"), "trade")


@hr_perm("labor.change_trade")
def trade_edit(request, pk):
    trade = get_object_or_404(Trade, pk=pk)
    form = TradeForm(request.POST or None, instance=trade)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Trade updated.")
        return redirect("web:labor_trade_list")
    return _form_page(request, form, f"Edit trade: {trade.name}", reverse("web:labor_trade_list"), "trade")


@hr_perm("labor.change_trade")
@require_POST
def trade_toggle(request, pk):
    trade = get_object_or_404(Trade, pk=pk)
    trade.is_active = not trade.is_active
    trade.save(update_fields=["is_active"])
    messages.success(request, f"{trade.name} was {'reactivated' if trade.is_active else 'deactivated'}. "
                              "Workers who already have it keep it.")
    return redirect("web:labor_trade_list")
