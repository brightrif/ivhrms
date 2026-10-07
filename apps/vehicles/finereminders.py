import logging
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import send_mass_mail
from django.db import transaction
from django.urls import NoReverseMatch, reverse
from django.utils import timezone

from .incidents import Fine, FineReminderLog

logger = logging.getLogger(__name__)
INTERVAL_DAYS = 7                                 # the first reminder when a fine is a week old, then every week


def finance_addresses(company_id):
    User = get_user_model()
    users = User.objects.filter(is_active=True, groups__name="Finance", company_access__company_id=company_id).distinct()
    return sorted({u.email for u in users if u.email})


def _link():
    try:
        return getattr(settings, "HRMS_BASE_URL", "").rstrip("/") + reverse("web:vehicle_fines")
    except NoReverseMatch:
        return ""


def build_digest(items, today):
    """One email per company listing every fine that is due a reminder, oldest first."""
    n = len(items)
    total = sum((f.amount for f, _ in items), Decimal("0"))
    lines = [f"{n} traffic fine{'s are' if n != 1 else ' is'} still unpaid ({total:.3f} BHD):", ""]
    for f, _week in items:
        who = f", driver {f.driver.full_name}" if f.driver_id else ""
        charged = " (charged to the driver)" if f.charged_to_employee else ""
        lines.append(f"- {f.vehicle.plate_number}, {f.offence}, {f.fined_on:%d %b %Y}: {f.amount:.3f} BHD, "
                     f"unpaid for {(today - f.fined_on).days} days{who}{charged}")
    link = _link()
    if link:
        lines += ["", f"Open in Ivhrms: {link}"]
    lines += ["", "You will get this reminder every week until the fines are paid.",
              "This is an automatic reminder from Ivhrms."]
    return f"[Vehicles] {n} unpaid traffic fine{'s' if n != 1 else ''} waiting for payment", "\n".join(lines)


def run_fine_scan(today=None):
    """Remind Finance, once a week, about every fine that is a week or more old and still unpaid. Fines of sold
    vehicles are included: they still have to be paid. Safe to re-run: a fine is reminded once per week."""
    today = today or timezone.localdate()
    stats = {"checked": 0, "alerts": 0, "emails": 0, "errors": 0}
    fines = (Fine.objects.filter(is_voided=False, status=Fine.Status.UNPAID, vehicle__company__is_active=True,
                                 fined_on__lte=today - timedelta(days=INTERVAL_DAYS))
             .select_related("vehicle", "driver").order_by("fined_on", "id"))
    due = defaultdict(list)
    for fine in fines:
        stats["checked"] += 1
        week = (today - fine.fined_on).days // INTERVAL_DAYS
        if not fine.reminders.filter(week=week).exists():
            due[fine.company_id].append((fine, week))
    for company_id, items in due.items():
        try:
            with transaction.atomic():
                addresses, sent = finance_addresses(company_id), 0
                if addresses:
                    subject, body = build_digest(items, today)
                    sent = send_mass_mail([(subject, body, None, [a]) for a in addresses])
                FineReminderLog.objects.bulk_create([FineReminderLog(fine=f, week=w, recipients=sent) for f, w in items])
            stats["alerts"] += len(items)
            stats["emails"] += sent
        except Exception:                           # one company failing must not stop the others
            logger.exception("Fine reminders failed for company %s", company_id)
            stats["errors"] += 1                    # nothing was logged, so the next run retries it
    return stats
