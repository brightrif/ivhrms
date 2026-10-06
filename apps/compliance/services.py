import calendar
import logging
from collections import defaultdict
from datetime import date
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import send_mass_mail
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.urls import NoReverseMatch, reverse
from django.utils import timezone

from apps.employees.models import Employee
from apps.organization.services import companies_for

from . import schedule
from .models import AlertLog, Document, DocumentType, RenewalPayment, RenewalTask

logger = logging.getLogger(__name__)
TaskStatus = RenewalTask.Status
UPCOMING_DAYS = 90


class ComplianceError(Exception):
    pass


def escalate_days():
    return getattr(settings, "COMPLIANCE_ESCALATE_DAYS", 7)


def add_months(d, months):
    index = d.month - 1 + months
    year, month = d.year + index // 12, index % 12 + 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


# ---------------------------------------------------------------- documents

def current_documents(user):
    """Current documents the user may see. Documents of people who have left the company, and of sold
    vehicles, are not chased."""
    return (Document.objects.filter(is_current=True, document_type__is_active=True)
            .for_user(user).select_related("company", "document_type", "responsible", "employee", "vehicle")
            .exclude(employee__status=Employee.Status.SEPARATED)
            .exclude(vehicle__status="sold"))                # "sold" = Vehicle.Status.SOLD, a literal to avoid an import




@transaction.atomic
def create_document(user, doc):
    try:
        doc.clean()                                   # company type <-> no employee, employee type <-> an employee
    except ValidationError as exc:
        raise ComplianceError(" ".join(exc.messages)) from exc
    if doc.employee_id and doc.employee.status == Employee.Status.SEPARATED:
        raise ComplianceError("This employee has left the company.")
    try:
        doc.save()
    except IntegrityError as exc:
        raise ComplianceError("A current document of this type with this reference already exists.") from exc
    return doc


@transaction.atomic
def update_document(doc, *, expiry_changed=False):
    doc.save()
    if expiry_changed:
        # A corrected expiry date restarts the alert schedule, otherwise old alerts would suppress new ones.
        doc.alerts.all().delete()
    return doc


@transaction.atomic
def renew_document(doc, user, *, expiry_date, number="", issue_date=None, file=None, notes=""):
    """Create the next version of a document, retire the old one and close its open renewal task."""
    old = Document.objects.select_for_update().get(pk=doc.pk)
    if not old.is_current:
        raise ComplianceError("This document has already been renewed.")
    if expiry_date <= old.expiry_date:
        raise ComplianceError(f"The new expiry date must be later than the current one ({old.expiry_date:%d %b %Y}).")
    if issue_date and expiry_date < issue_date:
        raise ComplianceError("The expiry date cannot be before the issue date.")
    if old.employee_id and old.employee.status == Employee.Status.SEPARATED:
        raise ComplianceError("This employee has left the company.")

    old.is_current = False
    old.save(update_fields=["is_current"])
    new = Document(company_id=old.company_id, employee_id=old.employee_id, document_type_id=old.document_type_id,
                   reference_name=old.reference_name, number=number or old.number, issue_date=issue_date,
                   expiry_date=expiry_date, responsible_id=old.responsible_id, agent_name=old.agent_name,
                   vehicle_id=old.vehicle_id,
                   notes=notes, previous=old)
    if file:
        new.file = file
    new.save()

    task = old.renewal_tasks.select_for_update().filter(status=TaskStatus.OPEN).first()
    if task:
        task.status, task.completed_at, task.new_document = TaskStatus.COMPLETED, timezone.now(), new
        task.save()
    return new


# ---------------------------------------------------------------- renewal tasks and payments

@transaction.atomic
def open_renewal(doc, *, assignee=None, due_date=None, estimated_cost=None, notes=""):
    doc = Document.objects.select_for_update().get(pk=doc.pk)       # never trust a stale copy
    if not doc.is_current:
        raise ComplianceError("Only the current version of a document can be renewed.")
    if doc.renewal_tasks.filter(status=TaskStatus.OPEN).exists():
        raise ComplianceError("A renewal is already open for this document.")
    return RenewalTask.objects.create(
        document=doc, assignee=assignee or doc.responsible, due_date=due_date or doc.expiry_date,
        estimated_cost=estimated_cost, notes=notes)


@transaction.atomic
def cancel_task(task):
    task = RenewalTask.objects.select_for_update().get(pk=task.pk)
    if task.status != TaskStatus.OPEN:
        raise ComplianceError("Only an open renewal can be cancelled.")
    task.status = TaskStatus.CANCELLED
    task.save()
    return task


@transaction.atomic
def record_payment(task, payment):
    """`payment` is an unsaved RenewalPayment. Payments can follow completion (receipts arrive late)."""
    if task.status == TaskStatus.CANCELLED:
        raise ComplianceError("This renewal was cancelled, so no payment can be recorded against it.")
    if payment.total <= 0:
        raise ComplianceError("Enter at least one amount greater than zero.")
    if payment.charged_to_employee and not task.document.employee_id:
        raise ComplianceError("Only the cost of an employee's document can be charged to the employee.")
    payment.task = task
    payment.save()
    return payment


# ---------------------------------------------------------------- dashboard and reports

def mandatory_missing(user):
    companies = list(companies_for(user))
    types = list(DocumentType.objects.filter(is_active=True, is_mandatory=True,
                                             applies_to=DocumentType.AppliesTo.COMPANY))
    held = set(Document.objects.filter(is_current=True, company__in=companies, document_type__in=types)
               .values_list("company_id", "document_type_id"))
    return [(c, t) for c in companies for t in types if (c.pk, t.pk) not in held]


def coverage(user):
    """For each required personal document type: how many active employees have a current one on file."""
    staff = Employee.objects.for_user(user).exclude(status=Employee.Status.SEPARATED)
    total = staff.count()
    types = list(DocumentType.objects.filter(is_active=True, is_mandatory=True,
                                             applies_to=DocumentType.AppliesTo.EMPLOYEE))
    if not total or not types:
        return []
    held = defaultdict(set)
    for type_id, employee_id in (Document.objects.filter(is_current=True, document_type__in=types, employee__in=staff)
                                 .values_list("document_type_id", "employee_id")):
        held[type_id].add(employee_id)
    return [{"type": t, "total": total, "have": len(held[t.pk]), "missing": total - len(held[t.pk]),
             "pct": round(100 * len(held[t.pk]) / total)} for t in types]


def employees_missing(user, dtype):
    """Active employees with no current document of this type, for HR to follow up (some may not need one)."""
    have = Document.objects.filter(is_current=True, document_type=dtype, employee__isnull=False).values("employee_id")
    return (Employee.objects.for_user(user).exclude(status=Employee.Status.SEPARATED).exclude(pk__in=have)
            .select_related("company").order_by("company__name", "employee_no"))


def dashboard(user):
    overdue, due, upcoming = [], [], []
    company_rows, employee_rows = [], []              # everything that needs a look, by whom it belongs to
    for doc in current_documents(user):
        state, left = doc.state, doc.days_left
        if state == schedule.EXPIRED:
            overdue.append(doc)
        elif state == schedule.DUE:
            due.append(doc)
        elif left <= UPCOMING_DAYS:
            upcoming.append(doc)
        else:
            continue
        (employee_rows if doc.employee_id else company_rows).append(doc)
    tasks = (RenewalTask.objects.for_user(user).filter(status=TaskStatus.OPEN)
             .select_related("company", "assignee", "document__document_type", "document__employee")
             .prefetch_related("payments"))
    return {"overdue": overdue, "due": due, "upcoming": upcoming, "open_tasks": list(tasks),
            "missing": mandatory_missing(user), "company_rows": company_rows, "employee_rows": employee_rows,
            "coverage": coverage(user)}


def attention_count(user):
    data = dashboard(user)
    return len(data["overdue"]) + len(data["due"]) + len(data["missing"])


def cost_report(user, year):
    """Payments in a year grouped by company and document type. Summed in Python (BHD has 3 decimals)."""
    rows = defaultdict(lambda: {"government": Decimal("0"), "service": Decimal("0"), "fine": Decimal("0"),
                                "charged": Decimal("0"), "count": 0})
    payments = (RenewalPayment.objects.for_user(user).filter(paid_on__year=year)
                .select_related("company", "task__document__document_type"))
    for p in payments:
        row = rows[(p.company, p.task.document.document_type)]
        row["government"] += p.government_fee
        row["service"] += p.service_fee
        row["fine"] += p.fine
        row["count"] += 1
        if p.charged_to_employee:
            row["charged"] += p.total
    table, grand = [], {"government": Decimal("0"), "service": Decimal("0"), "fine": Decimal("0"),
                        "charged": Decimal("0")}
    for (company, dtype), r in sorted(rows.items(), key=lambda kv: (kv[0][0].name, kv[0][1].name)):
        r["total"] = r["government"] + r["service"] + r["fine"]
        table.append({"company": company, "type": dtype, **r})
        for k in grand:
            grand[k] += r[k]
    grand["total"] = grand["government"] + grand["service"] + grand["fine"]
    return {"rows": table, "grand": grand}


def payment_years(user):
    dates = RenewalPayment.objects.for_user(user).values_list("paid_on", flat=True)
    return sorted({d.year for d in dates}, reverse=True)


# ---------------------------------------------------------------- alerts

def recipients(doc, escalate):
    """The responsible person plus HR and Finance users for the company (and Management when escalated)."""
    User = get_user_model()
    groups = ["HR", "Finance"] + (["Management"] if escalate else [])
    ids = set(User.objects.filter(is_active=True, groups__name__in=groups,
                                  company_access__company_id=doc.company_id).values_list("pk", flat=True))
    if doc.responsible_id:
        ids.add(doc.responsible_id)
    return list(User.objects.filter(pk__in=ids, is_active=True))


def _link(doc):
    try:
        path = reverse("web:compliance_detail", args=[doc.pk])
    except NoReverseMatch:
        return ""
    return settings.HRMS_BASE_URL.rstrip("/") + path


def build_message(doc, days_left):
    name = f"{doc.label} - {doc.subject_label}"
    if days_left > 0:
        subject = f"[Compliance] {name} expires in {days_left} day{'s' if days_left != 1 else ''}"
        when = f"expires on {doc.expiry_date:%d %b %Y} ({days_left} day{'s' if days_left != 1 else ''} from now)"
    elif days_left == 0:
        subject = f"[Compliance] {name} expires TODAY"
        when = f"expires today, {doc.expiry_date:%d %b %Y}"
    else:
        subject = f"[Compliance] OVERDUE: {name} expired {-days_left} day{'s' if days_left != -1 else ''} ago"
        when = f"EXPIRED on {doc.expiry_date:%d %b %Y} ({-days_left} day{'s' if days_left != -1 else ''} ago)"
    lines = [f"{name} {when}.", ""]
    if doc.employee_id:
        lines.append(f"Company: {doc.company.name}")
    if doc.number:
        lines.append(f"Number: {doc.number}")
    if doc.responsible:
        lines.append(f"Responsible: {doc.responsible.get_full_name() or doc.responsible.username}")
    if doc.agent_name:
        lines.append(f"Handled by: {doc.agent_name}")
    link = _link(doc)
    if link:
        lines += ["", f"Open in Ivhrms: {link}"]
    lines += ["", "This is an automatic reminder from Ivhrms."]
    return subject, "\n".join(lines)


def _send(doc, users, days_left):
    addresses = [u.email for u in users if u.email]
    if not addresses:
        return 0                                   # nobody has an email: the dashboard still shows it
    subject, body = build_message(doc, days_left)
    return send_mass_mail([(subject, body, None, [a]) for a in addresses])


def run_daily_scan(today=None):
    """Send every alert that is due and has not been sent. Safe to run any number of times a day."""
    today = today or timezone.localdate()
    stats = {"checked": 0, "alerts": 0, "emails": 0, "tasks": 0, "errors": 0}
    docs = (Document.objects.filter(is_current=True, document_type__is_active=True, company__is_active=True)
            .exclude(employee__status=Employee.Status.SEPARATED)
            .exclude(vehicle__status="sold")                 # a sold vehicle's documents are not chased
            .select_related("company", "document_type", "responsible", "employee", "vehicle"))
    for doc in docs:
        stats["checked"] += 1
        left = (doc.expiry_date - today).days
        bucket = schedule.due_bucket(left, doc.document_type.alert_days, doc.document_type.overdue_repeat_days)
        if bucket is None or doc.alerts.filter(bucket=bucket).exists():
            continue
        try:
            with transaction.atomic():
                if not doc.alerts.exists() and not doc.renewal_tasks.exists():
                    open_renewal(doc)               # the first alert also opens the renewal to-do
                    stats["tasks"] += 1
                sent = _send(doc, recipients(doc, escalate=left <= escalate_days()), left)
                AlertLog.objects.create(document=doc, bucket=bucket, recipients=sent)
            stats["alerts"] += 1
            stats["emails"] += sent
        except Exception:                           # one failing document must not stop the others
            logger.exception("Compliance alert failed for document %s", doc.pk)
            stats["errors"] += 1                    # nothing was logged, so tomorrow's run retries it
    return stats