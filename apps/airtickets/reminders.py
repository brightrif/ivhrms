"""Reminders before an air ticket falls due.

For every employee's open "next ticket" (and no request already in progress):
  * LEAD: the ticket falls due within the company's reminder window (Settings, Air Tickets), and
  * DUE : it fell due in the last few days.
A ticket that has been overdue for longer is NOT reminded about: on the first day this runs, nobody gets a flood of
mail about tickets that were due months ago. They are all on the Eligibility list and the report.

HR users of the company get ONE summary email per day listing everybody newly reminded; the employee gets their own."""
import logging
from collections import defaultdict
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import send_mass_mail
from django.db import transaction
from django.db.models import Exists, OuterRef
from django.urls import NoReverseMatch, reverse
from django.utils import timezone

from apps.configuration import services as settings_service
from apps.employees.models import Employee

from .models import TicketEntitlement
from .reminderlog import TicketReminderLog
from .ticketrequests import TicketRequest

logger = logging.getLogger(__name__)
DUE_GRACE_DAYS = 14                 # a ticket that fell due longer ago than this is not reminded about
Stage = TicketReminderLog.Stage


def hr_addresses(company_id):
    User = get_user_model()
    users = User.objects.filter(is_active=True, groups__name="HR", company_access__company_id=company_id).distinct()
    return sorted({u.email for u in users if u.email})


def _link(name):
    base = getattr(settings, "HRMS_BASE_URL", "").rstrip("/")
    try:
        return base + reverse(name) if base else ""
    except NoReverseMatch:
        return ""


def candidates(today):
    """Open entitlements worth a reminder, as (entitlement, stage, days until due)."""
    in_progress = TicketRequest.objects.filter(employee=OuterRef("employee"), status__in=TicketRequest.OPEN)
    rows = (TicketEntitlement.objects.filter(status=TicketEntitlement.Status.OPEN,
                                             employee__worker_type=Employee.WorkerType.STAFF,
                                             company__is_active=True)
            .exclude(employee__status=Employee.Status.SEPARATED).filter(~Exists(in_progress))
            .select_related("employee", "company").order_by("eligible_from", "employee__employee_no"))
    window = {}
    sent = set(TicketReminderLog.objects.filter(entitlement__in=[r.pk for r in rows])
               .values_list("entitlement_id", "stage", "due_on"))
    out = []
    for ent in rows:
        if ent.company_id not in window:
            window[ent.company_id] = settings_service.get("airtickets.reminder_days", ent.company) or 0
        days = (ent.eligible_from - today).days
        if not window[ent.company_id]:
            continue
        if 0 < days <= window[ent.company_id]:
            stage = Stage.LEAD
        elif -DUE_GRACE_DAYS <= days <= 0:
            stage = Stage.DUE
        else:
            continue
        if (ent.pk, stage, ent.eligible_from) not in sent:
            out.append((ent, stage, days))
    return out


def _when(days):
    if days > 0:
        return f"in {days} day{'s' if days != 1 else ''}"
    return "today" if days == 0 else f"{-days} day{'s' if days != -1 else ''} ago"


def hr_message(company, items):
    n = len(items)
    subject = f"[Air tickets] {n} employee{'s' if n != 1 else ''} with a ticket coming up ({company.name})"
    lines = [f"These employees of {company.name} have an air ticket that is coming up or has just fallen due:", ""]
    for ent, stage, days in items:
        e = ent.employee
        lines.append(f"  {e.full_name} ({e.employee_no}): due {ent.eligible_from:%d %b %Y} ({_when(days)})")
    link = _link("web:employee_tickets")
    if link:
        lines += ["", f"Open the list in Ivhrms: {link}?show=soon"]
    lines += ["", "This is an automatic summary from Ivhrms."]
    return subject, "\n".join(lines)


def employee_message(ent, days):
    e = ent.employee
    if days > 0:
        subject = f"Your air ticket is due on {ent.eligible_from:%d %b %Y}"
        first = f"Your air ticket falls due on {ent.eligible_from:%d %b %Y} ({_when(days)})."
    else:
        subject = "Your air ticket is due now"
        first = f"Your air ticket fell due on {ent.eligible_from:%d %b %Y} ({_when(days)})."
    lines = [f"Dear {e.first_name},", "", first, "You can request it in Ivhrms, under My air ticket."]
    link = _link("web:my_tickets")
    if link:
        lines += ["", link]
    lines += ["", "This is an automatic message from Ivhrms."]
    return subject, "\n".join(lines)


def run_reminders(today=None):
    """Send what is due and has not been sent. Safe to run any number of times a day."""
    today = today or timezone.localdate()
    stats = {"checked": 0, "reminded": 0, "hr_emails": 0, "employee_emails": 0, "errors": 0}
    by_company = defaultdict(list)
    for item in candidates(today):
        stats["checked"] += 1
        by_company[item[0].company].append(item)
    for company, items in by_company.items():
        try:
            with transaction.atomic():
                messages, recipients = [], defaultdict(int)
                addresses = hr_addresses(company.pk)
                if addresses:
                    subject, body = hr_message(company, items)
                    messages += [(subject, body, None, [a]) for a in addresses]
                    stats["hr_emails"] += len(addresses)
                if settings_service.get("airtickets.remind_employee", company):
                    for ent, stage, days in items:
                        if ent.employee.email:
                            subject, body = employee_message(ent, days)
                            messages.append((subject, body, None, [ent.employee.email]))
                            recipients[ent.pk] += 1
                            stats["employee_emails"] += 1
                if messages:
                    send_mass_mail(messages)
                for ent, stage, days in items:
                    TicketReminderLog.objects.create(entitlement=ent, stage=stage, due_on=ent.eligible_from,
                                                     recipients=len(addresses) + recipients[ent.pk])
                stats["reminded"] += len(items)
        except Exception:                         # one failing company must not stop the others
            logger.exception("Air ticket reminders failed for company %s", company.pk)
            stats["errors"] += 1
    return stats
