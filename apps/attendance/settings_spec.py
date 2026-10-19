"""What the Settings page shows for Attendance, and the rights the Access grid offers for it.

One tab for everything that decides how attendance works: the staff attendance sheet, the weekly off and the public
holidays. The weekly off and the holidays are dated and affect attendance and pay, so they are not simple switches:
they have their own pages, and the tab shows what is in force with a link to them."""
from datetime import date

from apps.configuration.registry import Capability, Module, Section, Setting, register
from apps.organization.services import companies_for


def weekly_off_context(user):
    from apps.scheduling.calendar_rules import weekday_names, weekly_off_on
    from apps.scheduling.weekly_off import WeeklyOffRule

    today = date.today()
    rows = []
    for company in companies_for(user):
        later = (WeeklyOffRule.objects.filter(company=company, effective_from__gt=today)
                 .order_by("effective_from").first())
        rows.append({"company": company, "now": weekday_names(weekly_off_on(company.pk, today)),
                     "later": {"text": weekday_names(later.days), "from": later.effective_from} if later else None})
    return {"weekly_off_rows": rows}


def holiday_context(user):
    from django.db.models import Q

    from apps.scheduling.models import Holiday

    rows = (Holiday.objects.filter(date__gte=date.today(), is_active=True)
            .filter(Q(company__in=companies_for(user)) | Q(company__isnull=True)).select_related("company")
            .order_by("date")[:5])
    return {"holiday_rows": list(rows)}


register(Module(
    key="attendance", label="Attendance", order=43, icon="bi-calendar-check",
    sections=[
        Section(title="Staff attendance sheet", width="col-lg-4", settings=[
            Setting("attendance.staff_sheet_period", "Period it opens on", "choice", "week",
                    choices=(("day", "Day"), ("week", "Week (Sunday to Saturday)"), ("month", "Month")),
                    per_company=True,
                    help="What the Staff > Attendance grid opens on. It can always be switched with the Day, Week and "
                         "Month buttons."),
        ]),
        Section(
            title="Weekly off", width="col-lg-8",
            intro="The weekly off for each company. A change starts on a date you choose and earlier days keep the "
                  "weekly off they had. A person on a shift follows the shift's own weekly off days.",
            template="web/settings/_attendance_weekly_off.html", context=weekly_off_context),
        Section(
            title="Public holidays", width="col-12",
            intro="The holidays for every company or for one. They are paid rest days in attendance.",
            template="web/settings/_attendance_holidays.html", context=holiday_context),
    ],
    capabilities=[
        Capability("Give attendance for staff", "attendance.add_attendance",
                   "The Staff attendance page. A day that is already recorded never changes there, except a weekly "
                   "off or holiday the system recorded, which HR can replace when someone worked."),
        Capability("See the public holidays and the weekly off", "scheduling.view_holiday"),
        Capability("Add public holidays", "scheduling.add_holiday"),
        Capability("Rename and switch off public holidays", "scheduling.change_holiday"),
        Capability("Change the weekly off", "scheduling.add_weeklyoffrule", "Dated; earlier days keep their weekly off."),
    ],
))
