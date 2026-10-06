import json
from datetime import timedelta

from django.utils import timezone


def leave_calendar_hints(employee, *, days_back=60, days_ahead=420):
    """data-* attributes that let the leave picker shade weekly offs and mark public holidays.

    They come from the same scheduling data the server uses to count leave days, so what the person
    sees on the calendar matches what is charged.
    """
    from apps.scheduling import services as scheduling

    today = timezone.localdate()
    shift = scheduling.shift_on(employee, today)
    python_days = shift.weekly_off_days if shift else scheduling.default_weekly_off()
    js_days = sorted({(day + 1) % 7 for day in python_days})        # Python: Mon=0..Sun=6; JavaScript: Sun=0..Sat=6
    holidays = scheduling.holidays_between(
        employee.company_id, today - timedelta(days=days_back), today + timedelta(days=days_ahead))
    return {
        "data-off-days": ",".join(str(d) for d in js_days),
        "data-holidays": json.dumps({d.isoformat(): h.name for d, h in holidays.items()}),
    }
