from datetime import date
from urllib.parse import urlencode

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import Count
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone

from apps.labor import overtime_services as ot
from apps.labor import sitesheet
from apps.labor.overtime import OvertimeClaim, OvertimePolicy
from apps.labor.services import LaborError
from apps.organization.services import companies_for

from apps.web.access import hr_perm
from apps.web.forms.labor_overtime import OvertimePolicyForm

# what each button needs. HR prepares claims, Management decides them, and voiding (so hours can be reopened) needs
# the delete permission, which no group has by default.
ACTION_PERMS = {"prepare": "labor.add_overtimeclaim", "approve": "labor.change_overtimeclaim",
                "reject": "labor.change_overtimeclaim", "void": "labor.delete_overtimeclaim"}
LAYOUTS = {"policy": [
    {"title": "Rules start", "width": "col-lg-4", "rows": [["company"], ["effective_from"], ["overtime_applies"]]},
    {"title": "Pay for each overtime hour (times the hourly rate)", "width": "col-lg-4",
     "rows": [["working_day_multiplier"], ["weekly_off_multiplier"], ["holiday_multiplier"]]},
    {"title": "How it is counted", "width": "col-lg-4", "rows": [["all_hours_on_days_off"], ["monthly_divisor"]]},
]}


def _anchor(value, today):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return today


@hr_perm("labor.view_overtimeclaim")
def labor_overtime(request):
    """Overtime claims for a site and period: prepared by HR from confirmed hours, decided by Management."""
    today = timezone.localdate()
    data = request.POST if request.method == "POST" else request.GET
    period = data.get("period") if data.get("period") in sitesheet.PERIODS else "month"
    anchor = min(_anchor(data.get("date"), today), today)
    first, last = sitesheet.period_bounds(period, anchor)
    sites = sitesheet.sites_in_period(request.user, first, last)
    chosen = next((s for s in sites if f"{s[0].pk}:{s[1].pk}" == data.get("site")), sites[0] if sites else None)
    here = {"period": period, "date": anchor.isoformat(), **({"site": f"{chosen[0].pk}:{chosen[1].pk}"} if chosen else {})}
    claims_qs = (OvertimeClaim.objects.for_user(request.user).filter(project=chosen[0], location=chosen[1],
                                                                     date__range=(first, last))
                 if chosen else OvertimeClaim.objects.none())

    if request.method == "POST":
        action = request.POST.get("action")
        if action not in ACTION_PERMS or not request.user.has_perm(ACTION_PERMS[action]):
            raise PermissionDenied
        if chosen is None:
            messages.error(request, "There is no site to work on in this period.")
        else:
            project, location = chosen
            try:
                if action == "prepare":
                    created, skipped = ot.prepare_claims(request.user, project, location, first, last)
                    if created:
                        messages.success(request, f"Prepared {created} overtime claim{'s' if created != 1 else ''} for approval.")
                    else:
                        messages.info(request, "No overtime to claim in the confirmed hours.")
                    if skipped:
                        messages.warning(request, "Not claimed: " + " | ".join(skipped[:5]))
                elif action in ("approve", "reject"):
                    ids = [i for i in request.POST.getlist("claims") if i.isdigit()]
                    if not ids:
                        raise LaborError("Tick the claims first.")
                    decided, skipped = ot.decide(request.user, claims_qs.filter(pk__in=ids), action == "approve",
                                                 request.POST.get("note", ""))
                    verb = "Approved" if action == "approve" else "Rejected"
                    messages.success(request, f"{verb} {decided} claim{'s' if decided != 1 else ''}."
                                     + (f" {skipped} already decided were left alone." if skipped else ""))
                else:
                    n = ot.void_claims(request.user, project, location, first, last)
                    messages.success(request, f"Voided {n} overtime claim{'s' if n != 1 else ''}. "
                                              "The timesheet can now be reopened.")
            except LaborError as exc:
                messages.error(request, str(exc))
        return redirect(f"{reverse('web:labor_overtime')}?{urlencode(here)}")

    claims = list(claims_qs.select_related("employee", "employee__labor_profile__trade", "work_order")
                  .order_by("employee__employee_no", "date"))
    mode, policy = ot.overtime_mode(chosen[0].company, last) if chosen else ("applies", None)
    nav = lambda step: urlencode({**here, "date": sitesheet.shift_anchor(period, first, step).isoformat()})
    has = request.user.has_perm
    return render(request, "web/labor/overtime.html", {
        "claims": claims, "summary": ot.summary(claims), "policy": policy, "mode": mode, "sites": sites, "chosen": chosen,
        "period": period, "periods": sitesheet.PERIODS.items(), "anchor": anchor, "first": first, "last": last,
        "today": today, "prev_query": nav(-1), "next_query": nav(1),
        "next_is_future": sitesheet.shift_anchor(period, first, 1) > today,
        "pending": sum(1 for c in claims if c.status == "pending"),
        "can_prepare": has(ACTION_PERMS["prepare"]), "can_decide": has(ACTION_PERMS["approve"]),
        "can_void": has(ACTION_PERMS["void"]), "can_see_rules": has("labor.view_overtimepolicy")})


@hr_perm("labor.view_overtimepolicy")
def labor_overtime_rules(request):
    companies = companies_for(request.user)
    policies = (OvertimePolicy.objects.for_user(request.user).select_related("company")
                .annotate(claim_count=Count("claims")).order_by("company__code", "-effective_from"))
    have = {p.company_id for p in policies}
    return render(request, "web/labor/overtime_rules.html", {
        "policies": policies, "multi_company": companies.count() > 1,
        "without_rules": [c for c in companies if c.pk not in have]})


@hr_perm("labor.add_overtimepolicy")
def labor_overtime_rules_new(request):
    form = OvertimePolicyForm(request.POST or None, user=request.user)
    if request.method == "POST" and form.is_valid():
        cd = form.cleaned_data
        try:
            ot.set_policy(cd["company"], effective_from=cd["effective_from"],
                          working_day_multiplier=cd["working_day_multiplier"],
                          weekly_off_multiplier=cd["weekly_off_multiplier"], holiday_multiplier=cd["holiday_multiplier"],
                          all_hours_on_days_off=cd["all_hours_on_days_off"], monthly_divisor=cd["monthly_divisor"],
                          overtime_applies=cd["overtime_applies"])
        except LaborError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "Saved. Earlier rules are kept for the claims made under them.")
            return redirect("web:labor_overtime_rules")
    return render(request, "web/form_layout.html", {
        "form": form, "title": "Overtime rules", "back_url": reverse("web:labor_overtime_rules"),
        "layout": LAYOUTS["policy"],
        "intro": "Tick or untick whether this company pays overtime, and set the rates to match your contracts and the "
                 "Labour Law. The starting numbers are only suggestions."})
