"""The weekly off and the public holidays: reading them for a stretch of days, and changing them.

Order of precedence for a day: a public holiday, then the weekly off of the person's shift (if they are on one), then
the company's weekly-off rule in force that day, then the built-in default (Friday)."""
from datetime import timedelta

from django.db import transaction
from django.db.models import Q

from .models import Holiday, default_weekly_off
from .weekly_off import WeeklyOffRule

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
MAX_HOLIDAY_SPAN = 31


class CalendarError(Exception):
    pass


def company_weekly_off(company_id, start, end):
    """{date: [weekday numbers]}: the company's weekly off on each day, from the rule in force that day."""
    rules = list(WeeklyOffRule.objects.filter(company_id=company_id, effective_from__lte=end)
                 .filter(Q(effective_to__isnull=True) | Q(effective_to__gte=start)))
    fallback = default_weekly_off()
    found, d = {}, start
    while d <= end:
        days = fallback
        for rule in rules:
            if rule.effective_from <= d and (rule.effective_to is None or d <= rule.effective_to):
                days = rule.days
                break
        found[d] = days
        d += timedelta(days=1)
    return found


def weekly_off_on(company_id, d):
    return company_weekly_off(company_id, d, d)[d]


def weekday_names(days):
    return ", ".join(WEEKDAYS[d] for d in sorted(days)) or "none"


def current_rule(company):
    return WeeklyOffRule.objects.filter(company=company, effective_to__isnull=True).first()


@transaction.atomic
def set_weekly_off(company, days, effective_from):
    """Start a new weekly off on a date. The rule before it ends the day before; earlier days are never touched."""
    try:
        days = sorted({int(d) for d in days})
    except (TypeError, ValueError):
        raise CalendarError("Choose the days of the week.") from None
    if not days:
        raise CalendarError("Choose at least one day.")
    if any(not 0 <= d <= 6 for d in days):
        raise CalendarError("Choose days from Monday to Sunday.")
    if len(days) == 7:
        raise CalendarError("A week needs at least one working day.")
    current = WeeklyOffRule.objects.select_for_update().filter(company=company, effective_to__isnull=True).first()
    if current:
        if effective_from <= current.effective_from:
            raise CalendarError(f"The new weekly off must start after the current one, which began on "
                                f"{current.effective_from:%d %b %Y}.")
        if sorted(current.days) == days:
            raise CalendarError(f"The weekly off is already {weekday_names(days)}.")
        current.effective_to = effective_from - timedelta(days=1)
        current.save()
    elif days == sorted(default_weekly_off()):
        raise CalendarError(f"The weekly off is already {weekday_names(days)}.")
    return WeeklyOffRule.objects.create(company=company, days=days, effective_from=effective_from)


@transaction.atomic
def add_holidays(company, first, last, name, name_ar=""):
    """One holiday row per day from `first` to `last` (a one-day holiday leaves `last` empty). `company` None means
    every company. Refuses if any of those days already has a holiday in the same scope, so nothing is half-added."""
    last = last or first
    name = " ".join((name or "").split())
    if not name:
        raise CalendarError("Give the holiday a name.")
    if last < first:
        raise CalendarError("The last day cannot be before the first day.")
    if (last - first).days + 1 > MAX_HOLIDAY_SPAN:
        raise CalendarError(f"A holiday can span at most {MAX_HOLIDAY_SPAN} days.")
    days = [first + timedelta(days=i) for i in range((last - first).days + 1)]
    taken = sorted(Holiday.objects.filter(company=company, date__in=days).values_list("date", flat=True))
    if taken:
        raise CalendarError("There is already a holiday on " + ", ".join(f"{d:%d %b %Y}" for d in taken)
                            + ". Switch it off or edit it instead.")
    return [Holiday.objects.create(company=company, date=d, name=name, name_ar=(name_ar or "").strip())
            for d in days]


def set_holiday_active(holiday, active):
    if holiday.is_active != active:
        holiday.is_active = active
        holiday.save()
    return holiday


def rename_holiday(holiday, name, name_ar=""):
    name = " ".join((name or "").split())
    if not name:
        raise CalendarError("Give the holiday a name.")
    holiday.name, holiday.name_ar = name, (name_ar or "").strip()
    holiday.save()
    return holiday
