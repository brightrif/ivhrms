"""Settings: the public holidays and the weekly off. A holiday is added for one company or for every company; the
weekly off is a company setting that starts on a date, so earlier days keep the weekly off they had."""
from datetime import date

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.organization.services import companies_for
from apps.scheduling import calendar_rules
from apps.scheduling.models import Holiday
from apps.scheduling.weekly_off import WeeklyOffRule

from apps.web.access import hr_perm
from apps.web.forms.scheduling_settings import HolidayEditForm, HolidayForm, WeeklyOffForm
from apps.web.views.settings_hub import _visible


def _page(request, template, context):
    context.update(tabs=_visible(request.user), tab="attendance", show_access=request.user.is_superuser)
    return render(request, template, context)


def _holiday(request, pk):
    """A holiday the person may change: one of their companies', or a global one if they are a superuser."""
    holiday = get_object_or_404(Holiday.objects.select_related("company"), pk=pk)
    if holiday.company_id is None:
        if not request.user.is_superuser:
            raise PermissionDenied
    elif not companies_for(request.user).filter(pk=holiday.company_id).exists():
        raise PermissionDenied
    return holiday


@hr_perm("scheduling.view_holiday")
def holiday_list(request):
    today = timezone.localdate()
    raw = request.GET.get("year", "")
    year = int(raw) if raw.isdigit() and 2000 <= int(raw) <= 2100 else today.year
    companies = companies_for(request.user)
    rows = (Holiday.objects.filter(date__year=year).filter(Q(company__in=companies) | Q(company__isnull=True))
            .select_related("company").order_by("date", "company_id"))
    return _page(request, "web/settings/holidays.html", {
        "rows": rows, "year": year, "years": range(today.year - 1, today.year + 3),
        "multi_company": companies.count() > 1})


@hr_perm("scheduling.add_holiday")
def holiday_add(request):
    form = HolidayForm(request.POST or None, user=request.user,
                       initial={"applies_to": "all" if request.user.is_superuser else None})
    if request.method == "POST" and form.is_valid():
        cd = form.cleaned_data
        company = None if cd["applies_to"] == "all" else companies_for(request.user).filter(pk=cd["applies_to"]).first()
        if cd["applies_to"] != "all" and company is None:
            form.add_error("applies_to", "Choose one of your companies.")
        else:
            try:
                made = calendar_rules.add_holidays(company, cd["first_date"], cd["last_date"], cd["name"], cd["name_ar"])
            except calendar_rules.CalendarError as exc:
                form.add_error(None, str(exc))
            else:
                messages.success(request, f"{cd['name']} was added ({len(made)} day{'s' if len(made) != 1 else ''}). "
                                          "Days already recorded for attendance are not changed.")
                return redirect(f"{reverse('web:holiday_list')}?year={cd['first_date'].year}")
    return _page(request, "web/settings/holiday_form.html", {"form": form, "editing": None})


@hr_perm("scheduling.change_holiday")
def holiday_edit(request, pk):
    holiday = _holiday(request, pk)
    form = HolidayEditForm(request.POST or None, initial={"name": holiday.name, "name_ar": holiday.name_ar})
    if request.method == "POST" and form.is_valid():
        try:
            calendar_rules.rename_holiday(holiday, form.cleaned_data["name"], form.cleaned_data["name_ar"])
        except calendar_rules.CalendarError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "The holiday was renamed.")
            return redirect(f"{reverse('web:holiday_list')}?year={holiday.date.year}")
    return _page(request, "web/settings/holiday_form.html", {"form": form, "editing": holiday})


@hr_perm("scheduling.change_holiday")
@require_POST
def holiday_toggle(request, pk):
    holiday = _holiday(request, pk)
    calendar_rules.set_holiday_active(holiday, not holiday.is_active)
    messages.success(request, f"{holiday.name} on {holiday.date:%d %b %Y} is now "
                              f"{'on' if holiday.is_active else 'switched off'}. Days already recorded are not changed.")
    return redirect(f"{reverse('web:holiday_list')}?year={holiday.date.year}")


@hr_perm("scheduling.view_holiday")
def weekly_off(request):
    today = timezone.localdate()
    form = WeeklyOffForm(request.POST or None, user=request.user)
    if request.method == "POST":
        if not request.user.has_perm("scheduling.add_weeklyoffrule"):
            raise PermissionDenied
        if form.is_valid():
            cd = form.cleaned_data
            try:
                rule = calendar_rules.set_weekly_off(form.companies[cd["company"]], cd["days"], cd["effective_from"])
            except calendar_rules.CalendarError as exc:
                form.add_error(None, str(exc))
            else:
                messages.success(request, f"The weekly off is {calendar_rules.weekday_names(rule.days)} from "
                                          f"{rule.effective_from:%d %b %Y}. Earlier days keep the weekly off they had.")
                return redirect("web:weekly_off")
    rows = []
    for company in companies_for(request.user):
        days = calendar_rules.weekly_off_on(company.pk, today)
        rows.append({"company": company, "now": calendar_rules.weekday_names(days),
                     "rules": [{"text": calendar_rules.weekday_names(r.days), "from": r.effective_from,
                                "to": r.effective_to, "later": r.effective_from > today}
                               for r in WeeklyOffRule.objects.filter(company=company).order_by("-effective_from")],
                     "has_rule": WeeklyOffRule.objects.filter(company=company).exists()})
    return _page(request, "web/settings/weekly_off.html", {"form": form, "rows": rows, "today": today})
