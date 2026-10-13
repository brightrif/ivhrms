"""Air tickets: who is eligible, the policies behind it, and tickets already taken (HR)."""
from datetime import timedelta

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, F, OuterRef, Q, Subquery
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.airtickets import services
from apps.airtickets.models import TicketEntitlement, TicketPolicy, TravelProfile
from apps.airtickets.nationalities import canonical
from apps.organization.services import companies_for

from apps.web.access import hr_perm
from apps.web.forms.airtickets import PastTicketForm, PolicyForm, TravelProfileForm

Status = TicketEntitlement.Status
SOON_DAYS = 90

_WHAT = {"title": "What they get", "width": "col-lg-4",
         "rows": [["interval_months", "first_after_months"], ["spouse_covered", "max_children"], ["mode"],
                  ["allowance_amount"]]}
_ORDER = {"title": "Order", "width": "col-lg-3", "rows": [["priority"], ["is_active"]]}
_WHO_ROWS = [["name"], ["nationalities"], ["min_grade", "employment_types"]]
# Full-page layouts, side by side on a laptop screen. See form_layout.html.
LAYOUTS = {
    "policy": [{"title": "Who it applies to", "width": "col-lg-5", "rows": [["company"]] + _WHO_ROWS}, _WHAT, _ORDER],
    "policy_edit": [{"title": "Who it applies to", "width": "col-lg-5", "rows": _WHO_ROWS}, _WHAT, _ORDER],
    "profile": [{"title": "Travel", "width": "col-lg-6", "rows": [["home_country", "destination_airport"], ["notes"]]}],
    "past": [{"title": "Ticket already taken", "width": "col-lg-6",
              "rows": [["travel_date", "destination"], ["cost"], ["notes"]]}],
}


def _form_page(request, form, title, back_url, layout, intro="", submit_label="Save"):
    return render(request, "web/form_layout.html", {
        "form": form, "title": title, "back_url": back_url, "layout": LAYOUTS[layout], "intro": intro,
        "submit_label": submit_label})


def _employee(request, pk):
    qs = services.staff().for_user(request.user).select_related("company", "grade", "department")
    return get_object_or_404(qs, pk=pk)


def _detail(emp):
    return reverse("web:employee_tickets_detail", args=[emp.pk])


# ------------------------------------------------------------------ who is eligible

@hr_perm("airtickets.view_ticketentitlement")
def tickets_list(request):
    today = timezone.localdate()
    user = request.user
    companies = companies_for(user)
    open_ = TicketEntitlement.objects.filter(employee=OuterRef("pk"), status=Status.OPEN)
    taken = (TicketEntitlement.objects.filter(employee=OuterRef("pk"), status__in=(Status.USED, Status.PAID))
             .order_by("-used_on"))
    base = services.staff().for_user(user)
    blank, unknown = services.nationality_problems(base)
    qs = (base.select_related("company", "grade", "travel_profile")
          .annotate(next_due=Subquery(open_.values("eligible_from")[:1]),
                    last_travel=Subquery(taken.values("used_on")[:1]),
                    policy_name=Subquery(open_.values("policy__name")[:1])))

    q, company, show = request.GET.get("q", "").strip(), request.GET.get("company", ""), request.GET.get("show", "policy")
    if q:
        qs = qs.filter(Q(first_name__icontains=q) | Q(last_name__icontains=q) | Q(employee_no__icontains=q))
    if company.isdigit():
        qs = qs.filter(company_id=int(company))
    if show == "policy":
        qs = qs.filter(next_due__isnull=False)
    elif show == "eligible":
        qs = qs.filter(next_due__lte=today)
    elif show == "soon":
        qs = qs.filter(next_due__gt=today, next_due__lte=today + timedelta(days=SOON_DAYS))
    elif show == "nopolicy":
        qs = qs.filter(next_due__isnull=True)
    elif show == "unknown":
        qs = qs.filter(Q(nationality__in=[text for text, _ in unknown]) | Q(nationality=""))
    qs = qs.order_by(F("next_due").asc(nulls_last=True), "company__name", "employee_no")

    page = Paginator(qs, 25).get_page(request.GET.get("page"))
    for e in page:
        e.recognised = canonical(e.nationality) is not None
        e.days = (e.next_due - today).days if e.next_due else None
    params = request.GET.copy()
    params.pop("page", None)
    return render(request, "web/airtickets/list.html", {
        "page": page, "extra": params.urlencode(), "q": q, "company": company, "show": show,
        "companies": companies, "multi_company": companies.count() > 1, "soon_days": SOON_DAYS,
        "blank_nationality": blank, "unknown_nationalities": unknown,
        "shows": [("policy", "With a ticket policy"), ("eligible", "Eligible now"),
                  ("soon", f"Due within {SOON_DAYS} days"), ("nopolicy", "No policy applies"),
                  ("unknown", "Nationality missing or not recognised"), ("all", "Everyone")]})


@hr_perm("airtickets.view_ticketentitlement")
def tickets_detail(request, pk):
    emp = _employee(request, pk)
    policy = services.policy_for(emp)
    entitlements = list(emp.ticket_entitlements.select_related("policy"))
    current = next((e for e in entitlements if e.status == Status.OPEN), None)
    why = []
    if policy is None:
        for p in TicketPolicy.objects.filter(company=emp.company, is_active=True).select_related("min_grade"):
            why.append((p, p.mismatches(emp)))
    state = services.entitlement_state(current) if current else None
    return render(request, "web/airtickets/detail.html", {
        "emp": emp, "policy": policy, "entitlements": entitlements, "current": current, "state": state,
        "why": why, "recognised": canonical(emp.nationality) is not None,
        "profile": getattr(emp, "travel_profile", None),
        "can_profile": request.user.has_perm("airtickets.change_travelprofile"),
        "can_record": request.user.has_perm("airtickets.add_ticketentitlement")})


@hr_perm("airtickets.change_travelprofile")
def tickets_profile(request, pk):
    emp = _employee(request, pk)
    profile = TravelProfile.objects.filter(employee=emp).first() or TravelProfile(employee=emp)
    form = TravelProfileForm(request.POST or None, instance=profile)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Travel details saved.")
        return redirect(_detail(emp))
    return _form_page(request, form, f"Travel details: {emp.full_name}", _detail(emp), "profile",
                      intro=f"{emp.employee_no}, {emp.nationality or 'nationality not set'}")


@hr_perm("airtickets.add_ticketentitlement")
def tickets_past(request, pk):
    emp = _employee(request, pk)
    form = PastTicketForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            services.record_past_ticket(emp, travel_date=form.cleaned_data["travel_date"],
                                        destination=form.cleaned_data["destination"], cost=form.cleaned_data["cost"],
                                        notes=form.cleaned_data["notes"])
        except services.TicketError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "Ticket recorded. The next one has been worked out from that date.")
            return redirect(_detail(emp))
    return _form_page(request, form, f"A ticket already taken: {emp.full_name}", _detail(emp), "past",
                      intro="Use this for tickets taken before the system started or booked outside it. "
                            "Enter them oldest first: each one moves the next ticket on.",
                      submit_label="Record the ticket")


@hr_perm("airtickets.add_ticketentitlement")
@require_POST
def tickets_sync(request):
    """Work out the next ticket again, for one employee (from their page) or for everyone the person can see."""
    raw = request.POST.get("employee", "")
    if raw.isdigit():
        emp = _employee(request, int(raw))
        services.sync_entitlement(emp)
        messages.success(request, "Updated.")
        return redirect(_detail(emp))
    stats = services.sync_all(companies_for(request.user))
    messages.success(request, f"Updated {stats['checked']} staff: {stats['with_policy']} have a ticket policy, "
                              f"{stats['without_policy']} do not.")
    return redirect("web:employee_tickets")


# ------------------------------------------------------------------ policies

@hr_perm("airtickets.view_ticketpolicy")
def policies(request):
    rows = (TicketPolicy.objects.for_user(request.user).select_related("company", "min_grade")
            .annotate(covered=Count("entitlements", filter=Q(entitlements__status=Status.OPEN)))
            .order_by("company__name", "-is_active", "name"))
    companies = companies_for(request.user)
    return render(request, "web/airtickets/policies.html", {
        "rows": rows, "multi_company": companies.count() > 1,
        "can_add": request.user.has_perm("airtickets.add_ticketpolicy"),
        "can_change": request.user.has_perm("airtickets.change_ticketpolicy")})


def _after_policy_change(request, policy, message):
    stats = services.sync_all([policy.company])
    messages.success(request, f"{message} {stats['with_policy']} of {stats['checked']} staff now have an air "
                              "ticket entitlement.")


@hr_perm("airtickets.add_ticketpolicy")
def policy_add(request):
    form = PolicyForm(request.POST or None, user=request.user)
    if request.method == "POST" and form.is_valid():
        policy = form.save()
        _after_policy_change(request, policy, f"Policy '{policy.name}' saved.")
        return redirect("web:employee_tickets_policies")
    return _form_page(request, form, "Add a ticket policy", reverse("web:employee_tickets_policies"), "policy",
                      intro="Say who it applies to and what they get. When more than one policy fits a person, the "
                            "most specific wins (nationality, then grade, then contract type).")


@hr_perm("airtickets.change_ticketpolicy")
def policy_edit(request, pk):
    policy = get_object_or_404(TicketPolicy.objects.for_user(request.user).select_related("company"), pk=pk)
    form = PolicyForm(request.POST or None, instance=policy, user=request.user)
    if request.method == "POST" and form.is_valid():
        policy = form.save()
        _after_policy_change(request, policy, f"Policy '{policy.name}' saved.")
        return redirect("web:employee_tickets_policies")
    return _form_page(request, form, f"Edit: {policy.name}", reverse("web:employee_tickets_policies"),
                      "policy_edit", intro=policy.company.name)


@hr_perm("airtickets.change_ticketpolicy")
@require_POST
def policy_toggle(request, pk):
    policy = get_object_or_404(TicketPolicy.objects.for_user(request.user).select_related("company"), pk=pk)
    policy.is_active = not policy.is_active
    policy.save(update_fields=["is_active", "updated_at"])
    _after_policy_change(request, policy, f"Policy '{policy.name}' is now {'on' if policy.is_active else 'off'}.")
    return redirect("web:employee_tickets_policies")
