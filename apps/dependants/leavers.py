"""Family members whose sponsor is on notice or has left, and the daily notice to HR about them.

Nothing is blocked or cancelled by itself: a visa is closed with the authority by a person, so HR is told once when the
sponsor goes on notice and once more when they have left, and raises a cancellation request for each family member."""
import logging
from collections import defaultdict

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import send_mass_mail
from django.db import transaction
from django.db.models import Exists, OuterRef
from django.urls import NoReverseMatch, reverse

from apps.compliance.models import Document
from apps.employees.models import Employee

from .models import Dependant
from .visa import FamilyLeaverAlert, FamilyVisaRequest

logger = logging.getLogger(__name__)
VISA_TYPE_CODE = "family-visa"


def leaver_cases(user=None):
    """Sponsored family members with a current visa, whose sponsor is on notice or has left, and for whom no visa
    request is in progress. `user` limits it to the companies that person may see."""
    qs = Dependant.objects.for_user(user) if user is not None else Dependant.objects.all()
    has_visa = Document.objects.filter(dependant=OuterRef("pk"), is_current=True, document_type__code=VISA_TYPE_CODE)
    in_progress = FamilyVisaRequest.objects.filter(dependant=OuterRef("pk"), status__in=FamilyVisaRequest.OPEN)
    return (qs.filter(is_active=True,
                      employee__status__in=[Employee.Status.ON_NOTICE, Employee.Status.SEPARATED])
              .filter(Exists(has_visa)).filter(~Exists(in_progress)))


def hr_addresses(company_id):
    User = get_user_model()
    users = User.objects.filter(is_active=True, groups__name="HR", company_access__company_id=company_id).distinct()
    return sorted({u.email for u in users if u.email})


def _link():
    try:
        return getattr(settings, "HRMS_BASE_URL", "").rstrip("/") + reverse("web:employee_family") + "?leaver=1"
    except NoReverseMatch:
        return ""


def build_message(dependants, state):
    emp = dependants[0].employee
    names = ", ".join(d.full_name for d in dependants)
    if state == "notice":
        subject = f"[Family visas] {emp.full_name} is on notice and sponsors family visas"
        first = f"{emp.full_name} is on notice and still sponsors family visas."
    else:
        subject = f"[Family visas] {emp.full_name} has left and their family visas are still open"
        first = f"{emp.full_name} has left the company and their family visas are still open."
    lines = [first, f"Family members with a current visa: {names}.", "",
             "Raise a cancellation request for each, so the visas are closed with the authority and stop being tracked."]
    link = _link()
    if link:
        lines += ["", f"Open in Ivhrms: {link}"]
    lines += ["", "This is an automatic notice from Ivhrms."]
    return subject, "\n".join(lines)


def run_leaver_scan(today=None):
    """Tell HR once per state (on notice, left) about each sponsor's family. Safe to re-run any number of times."""
    stats = {"checked": 0, "alerts": 0, "emails": 0, "errors": 0}
    rows = (leaver_cases().filter(company__is_active=True).select_related("employee", "company")
            .order_by("employee_id", "id"))
    groups = defaultdict(list)
    for dep in rows:
        state = "left" if dep.employee.status == Employee.Status.SEPARATED else "notice"
        stats["checked"] += 1
        if not dep.leaver_alerts.filter(state=state).exists():
            groups[(dep.employee_id, state)].append(dep)
    for (_, state), deps in groups.items():
        try:
            with transaction.atomic():
                addresses = hr_addresses(deps[0].company_id)
                sent = 0
                if addresses:
                    subject, body = build_message(deps, state)
                    sent = send_mass_mail([(subject, body, None, [a]) for a in addresses])
                for dep in deps:
                    FamilyLeaverAlert.objects.create(dependant=dep, state=state, recipients=sent)
            stats["alerts"] += len(deps)
            stats["emails"] += sent
        except Exception:                           # one failing sponsor must not stop the others
            logger.exception("Family leaver notice failed for employee %s", deps[0].employee_id)
            stats["errors"] += 1
    return stats
