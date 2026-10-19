"""What the Settings page shows for Holidays and weekly off, and the rights the Access grid offers for it.

Both are dated and affect attendance and pay, so they are not simple switches: they have their own pages, and the tab
shows what is in force with a link to them."""
from datetime import date

from apps.configuration.registry import Capability, Module, Section, register
from apps.organization.services import companies_for


def weekly_off_context(user):
    from .calendar_rules import weekday_names, weekly_off_on

    today = date.today()
    return {"weekly_off_rows": [{"company": c, "now": weekday_names(weekly_off_on(c.pk, today))}
                                for c in companies_for(user)]}


def holiday_context(user):
    from django.db.models import Q

    from .models import Holiday

    rows = (Holiday.objects.filter(date__gte=date.today(), is_active=True)
            .filter(Q(company__in=companies_for(user)) | Q(company__isnull=True)).select_related("company")
            .order_by("date")[:5])
    return {"holiday_rows": list(rows)}


register(Module(
    key="scheduling", label="Holidays and weekly off", order=44, icon="bi-calendar-event",
    sections=[
        Section(
            title="Weekly off",
            intro="The weekly off day or days for each company. A change starts on a date you choose and earlier days "
                  "keep the weekly off they had. A person on a shift follows the shift's own weekly off days.",
            template="web/settings/_scheduling_weekly_off.html", context=weekly_off_context),
        Section(
            title="Public holidays",
            intro="The holidays for every company or for one. They are paid rest days in attendance.",
            template="web/settings/_scheduling_holidays.html", context=holiday_context),
    ],
    capabilities=[
        Capability("See the public holidays and the weekly off", "scheduling.view_holiday"),
        Capability("Add public holidays", "scheduling.add_holiday"),
        Capability("Rename and switch off public holidays", "scheduling.change_holiday"),
        Capability("Change the weekly off", "scheduling.add_weeklyoffrule", "Dated; earlier days keep their weekly off."),
    ],
))
