"""Family visa requests: ask, get approved (through the approval engine), process, pay."""
from collections import defaultdict
from decimal import Decimal

from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.utils import timezone

from apps.compliance import services as compliance
from apps.compliance.models import Document, DocumentType, RenewalTask
from apps.core import approvals
from apps.core.models import ApprovalRequest
from apps.employees.models import Employee

from . import services as family
from .defaults import FLOW_CODE
from .models import Dependant, DependantAttachment
from .visa import FamilyVisaRequest, VisaPayment

Kind, Status = FamilyVisaRequest.Kind, FamilyVisaRequest.Status
VISA_TYPE_CODE = "family-visa"
ZERO = Decimal("0")
# the paper that proves the relationship
PAPER_FOR = {Dependant.Relationship.SPOUSE: DependantAttachment.Kind.MARRIAGE_CERTIFICATE,
             Dependant.Relationship.CHILD: DependantAttachment.Kind.BIRTH_CERTIFICATE}


class VisaError(Exception):
    pass


def current_visa(dependant):
    return Document.objects.filter(dependant=dependant, is_current=True,
                                   document_type__code=VISA_TYPE_CODE).first()


def blocked_reason(dependant):
    """Why nothing can be requested for this person right now, or None."""
    if not dependant.is_active:
        return f"{dependant.full_name} is no longer sponsored."
    if dependant.visa_requests.filter(status__in=FamilyVisaRequest.OPEN).exists():
        return f"{dependant.full_name} already has a visa request in progress."
    return None


def allowed_kinds(dependant):
    """The kinds of request that make sense now. A cancellation is allowed even after the sponsor has left."""
    if blocked_reason(dependant):
        return []
    sponsor_left = dependant.employee.status == Employee.Status.SEPARATED
    if current_visa(dependant) is None:
        return [] if sponsor_left else [Kind.NEW]
    return [Kind.CANCELLATION] if sponsor_left else [Kind.RENEWAL, Kind.CANCELLATION]


@transaction.atomic
def request_visa(dependant, kind, *, requested_by, reason=""):
    dep = Dependant.objects.select_for_update().select_related("employee").get(pk=dependant.pk)
    emp, reason = dep.employee, (reason or "").strip()
    if kind not in Kind.values:
        raise VisaError("Choose what the request is for.")
    if message := blocked_reason(dep):
        raise VisaError(message)
    has_visa = current_visa(dep) is not None
    if kind == Kind.NEW and has_visa:
        raise VisaError(f"{dep.full_name} already has a current family visa. Ask for a renewal instead.")
    if kind != Kind.NEW and not has_visa:
        raise VisaError(f"{dep.full_name} has no current family visa to {'renew' if kind == Kind.RENEWAL else 'cancel'}. "
                        "Add the visa document first, or ask for a new visa.")
    if kind != Kind.CANCELLATION and emp.status == Employee.Status.SEPARATED:
        raise VisaError(f"{emp.full_name} has left the company.")
    if kind == Kind.CANCELLATION and not reason:
        raise VisaError("Give the reason for cancelling the visa.")

    req = FamilyVisaRequest.objects.create(company_id=dep.company_id, employee=emp, dependant=dep, kind=kind,
                                           reason=reason, requested_by=requested_by)
    try:
        approvals.submit(FLOW_CODE, req, employee=emp, requested_by=requested_by)
    except approvals.ApprovalError as exc:
        raise VisaError(str(exc)) from exc               # raising inside atomic() rolls the request back
    return req


@transaction.atomic
def finalize(approval):
    """Called when the approval finishes, whatever the outcome. Idempotent."""
    req = FamilyVisaRequest.objects.select_for_update().get(pk=approval.object_id)
    if req.status != Status.PENDING:
        return req
    req.status = {ApprovalRequest.Status.APPROVED: Status.APPROVED,
                  ApprovalRequest.Status.REJECTED: Status.REJECTED}.get(approval.status, Status.CANCELLED)
    req.save(update_fields=["status", "updated_at"])
    return req


@transaction.atomic
def cancel_request(request, user):
    ct = ContentType.objects.get_for_model(FamilyVisaRequest)
    approval = ApprovalRequest.objects.filter(content_type=ct, object_id=str(request.pk),
                                              status=ApprovalRequest.Status.PENDING).first()
    if approval is None:
        raise VisaError("This request is not waiting for approval.")
    try:
        approvals.cancel(approval.pk, user)              # finishes the approval, which withdraws the request
    except approvals.ApprovalError as exc:
        raise VisaError(str(exc)) from exc
    return FamilyVisaRequest.objects.get(pk=request.pk)


def _lock(request):
    return (FamilyVisaRequest.objects.select_for_update().select_related("dependant", "employee")
            .get(pk=request.pk))


@transaction.atomic
def mark_submitted(request, *, submitted_on, reference=""):
    req = _lock(request)
    if req.status != Status.APPROVED:
        raise VisaError("Only an approved request can be marked as submitted.")
    req.status, req.submitted_on, req.reference = Status.SUBMITTED, submitted_on, (reference or "").strip()
    req.save(update_fields=["status", "submitted_on", "reference", "updated_at"])
    return req


@transaction.atomic
def complete(request, user, *, completed_on, number="", issue_date=None, expiry_date=None):
    """Record the outcome. A new or renewed visa becomes a compliance document (so it is chased from then on); a
    cancelled one is retired and the family member is marked as no longer sponsored."""
    req = _lock(request)
    if req.status not in (Status.APPROVED, Status.SUBMITTED):
        raise VisaError("Only an approved request can be completed.")
    if completed_on > timezone.localdate():
        raise VisaError("The completion date cannot be in the future.")
    dep = req.dependant
    if req.kind == Kind.CANCELLATION:
        _retire(dep, completed_on)
    else:
        if not expiry_date:
            raise VisaError("Enter the expiry date of the visa.")
        try:
            if req.kind == Kind.NEW:
                dtype = DocumentType.objects.filter(code=VISA_TYPE_CODE, is_active=True).first()
                if dtype is None:
                    raise VisaError("The 'Family visa' document type is missing or switched off.")
                req.visa_document = compliance.create_document(user, Document(
                    dependant=dep, document_type=dtype, number=number, issue_date=issue_date,
                    expiry_date=expiry_date, responsible=user))
            else:
                old = current_visa(dep)
                if old is None:
                    raise VisaError(f"{dep.full_name} has no current family visa to renew.")
                req.visa_document = compliance.renew_document(old, user, expiry_date=expiry_date, number=number,
                                                              issue_date=issue_date)
        except compliance.ComplianceError as exc:
            raise VisaError(str(exc)) from exc
    req.status, req.completed_on = Status.COMPLETED, completed_on
    req.save(update_fields=["status", "completed_on", "visa_document", "updated_at"])
    return req


def _retire(dependant, cancelled_on):
    visa = current_visa(dependant)
    if visa is not None:
        visa.is_current = False
        visa.save(update_fields=["is_current"])
    for task in RenewalTask.objects.filter(document__dependant=dependant, status=RenewalTask.Status.OPEN):
        compliance.cancel_task(task)
    try:
        family.deactivate_dependant(dependant, left_on=cancelled_on)
    except family.DependantError:
        pass                                              # already marked as no longer sponsored


@transaction.atomic
def add_payment(request, payment):
    """`payment` is an unsaved VisaPayment. Costs can follow completion (invoices arrive late)."""
    req = FamilyVisaRequest.objects.select_for_update().get(pk=request.pk)
    if req.status not in (Status.APPROVED, Status.SUBMITTED, Status.COMPLETED):
        raise VisaError("Costs can be recorded once the request is approved.")
    if payment.total <= 0:
        raise VisaError("Enter at least one amount greater than zero.")
    payment.request = req
    payment.save()
    return payment


def checklist(dependant):
    """What a visa application needs for this person: the proof of relationship and the other documents."""
    items = []
    paper = PAPER_FOR.get(dependant.relationship)
    if paper:
        items.append({"label": DependantAttachment.Kind(paper).label,
                      "have": dependant.attachments.filter(kind=paper).exists()})
    held = set(Document.objects.filter(dependant=dependant, is_current=True).values_list("document_type_id", flat=True))
    types = (DocumentType.objects.filter(applies_to=DocumentType.AppliesTo.DEPENDANT, is_active=True, is_mandatory=True)
             .exclude(code=VISA_TYPE_CODE).order_by("name"))
    items += [{"label": t.name, "have": t.pk in held} for t in types]
    return items


def cost_report(user, year):
    """Visa costs in a year by company and kind of request. Summed in Python (BHD has 3 decimals)."""
    rows = defaultdict(lambda: {"government": ZERO, "service": ZERO, "fine": ZERO, "charged": ZERO, "count": 0})
    for p in (VisaPayment.objects.for_user(user).filter(paid_on__year=year).select_related("company", "request")):
        row = rows[(p.company, p.request.kind)]
        row["government"] += p.government_fee
        row["service"] += p.service_fee
        row["fine"] += p.fine
        row["count"] += 1
        if p.charged_to_employee:
            row["charged"] += p.total
    table, grand = [], {"government": ZERO, "service": ZERO, "fine": ZERO, "charged": ZERO}
    for (company, kind), r in sorted(rows.items(), key=lambda kv: (kv[0][0].name, kv[0][1])):
        r["total"] = r["government"] + r["service"] + r["fine"]
        table.append({"company": company, "kind": Kind(kind).label, **r})
        for k in grand:
            grand[k] += r[k]
    grand["total"] = grand["government"] + grand["service"] + grand["fine"]
    return {"rows": table, "grand": grand}


def payment_years(user):
    dates = VisaPayment.objects.for_user(user).values_list("paid_on", flat=True)
    return sorted({d.year for d in dates}, reverse=True)
