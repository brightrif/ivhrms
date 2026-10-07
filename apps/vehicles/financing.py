import logging
from decimal import Decimal
from types import SimpleNamespace

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import send_mass_mail
from django.db import IntegrityError, transaction
from django.urls import NoReverseMatch, reverse
from django.utils import timezone

from apps.compliance.services import add_months

from .loans import LoanAlertLog, LoanInstallment, VehicleLoan
from .models import Vehicle
from .services import VehicleError

logger = logging.getLogger(__name__)
Due, Paid, Settled = LoanInstallment.Status.DUE, LoanInstallment.Status.PAID, LoanInstallment.Status.SETTLED
ALERT_DAYS = 7                                    # tell Finance this many days before an installment falls due


def build_schedule(first_due_on, count, amount, final_amount=None):
    """(number, due date, amount) for each installment. Dates are counted from the first one, so a loan that
    starts on the 31st falls due on the last day of shorter months and on the 31st again afterwards."""
    return [(n, add_months(first_due_on, n - 1), final_amount if (final_amount is not None and n == count) else amount)
            for n in range(1, count + 1)]


def current_loan(vehicle):
    """The active loan, or else the most recent one that was not cancelled."""
    loans = list(vehicle.loans.filter(is_voided=False).order_by("-first_due_on", "-id"))
    return next((l for l in loans if l.status == VehicleLoan.Status.ACTIVE), loans[0] if loans else None)


def loan_summary(loan, today=None):
    today = today or timezone.localdate()
    rows = list(loan.installments.all())
    due = [r for r in rows if r.status == Due]
    paid = [r for r in rows if r.status == Paid]
    overdue = [r for r in due if r.due_on < today]
    return SimpleNamespace(
        rows=rows, paid_count=len(paid), left_count=len(due), overdue_count=len(overdue),
        paid_total=sum((r.paid_amount or Decimal("0") for r in paid), Decimal("0")),      # summed in Python: BHD has 3 decimals
        outstanding=sum((r.amount for r in due), Decimal("0")),
        overdue_total=sum((r.amount for r in overdue), Decimal("0")),
        next_due=min(due, key=lambda r: r.due_on) if due else None)


def overview(user, today=None):
    """Every active loan on the vehicles the user can see, most urgent first."""
    today = today or timezone.localdate()
    loans = (VehicleLoan.objects.for_user(user).filter(status=VehicleLoan.Status.ACTIVE, is_voided=False)
             .select_related("vehicle", "vehicle__company").prefetch_related("installments"))
    rows = []
    for loan in loans:
        loan.summary = loan_summary(loan, today)
        rows.append(loan)
    rows.sort(key=lambda l: (l.summary.next_due.due_on if l.summary.next_due else today, l.vehicle.plate_number))
    return {"loans": rows,
            "outstanding": sum((l.summary.outstanding for l in rows), Decimal("0")),
            "monthly": sum((l.installment_amount for l in rows), Decimal("0")),
            "overdue": sum(l.summary.overdue_count for l in rows)}


@transaction.atomic
def create_loan(vehicle, *, lender, financed_amount, installment_count, installment_amount, first_due_on,
                down_payment=Decimal("0"), account_no="", final_installment_amount=None, already_paid=0, notes=""):
    """Record a loan and build its schedule. `already_paid` marks the first installments as paid, for a loan that
    started before the system was used."""
    vehicle = Vehicle.objects.select_for_update().get(pk=vehicle.pk)
    today = timezone.localdate()
    if vehicle.status == Vehicle.Status.SOLD:
        raise VehicleError("A sold vehicle cannot take a new loan.")
    if vehicle.ownership == Vehicle.Ownership.LEASED:
        raise VehicleError("This vehicle is marked as leased. Change its ownership first if it is financed by a loan.")
    if not (lender or "").strip():
        raise VehicleError("Enter the bank or lender.")
    if not 1 <= installment_count <= 120:
        raise VehicleError("The number of installments must be between 1 and 120.")
    if financed_amount <= 0 or installment_amount <= 0:
        raise VehicleError("The amount financed and the installment must be more than zero.")
    if (final_installment_amount is not None and final_installment_amount <= 0) or down_payment < 0:
        raise VehicleError("The last installment must be more than zero and the down payment cannot be negative.")
    if not 0 <= already_paid <= installment_count:
        raise VehicleError("The installments already paid must be between none and the full number.")
    if vehicle.loans.filter(status=VehicleLoan.Status.ACTIVE, is_voided=False).exists():
        raise VehicleError("This vehicle already has an active loan. Settle or cancel it first.")

    loan = VehicleLoan(vehicle=vehicle, lender=lender.strip(), account_no=account_no.strip(), down_payment=down_payment,
                       financed_amount=financed_amount, installment_count=installment_count,
                       installment_amount=installment_amount, final_installment_amount=final_installment_amount,
                       first_due_on=first_due_on, notes=notes)
    if loan.scheduled_total < financed_amount:
        raise VehicleError(f"The installments add up to {loan.scheduled_total:.3f} BHD, which is less than the "
                           f"{financed_amount:.3f} BHD financed. Check the amounts.")
    schedule = build_schedule(first_due_on, installment_count, installment_amount, final_installment_amount)
    for number, due_on, amount in schedule[:already_paid]:
        if due_on > today:
            raise VehicleError(f"Installment {number} falls due on {due_on:%d %b %Y}, which is in the future, "
                               "so it cannot already be paid.")
    try:
        loan.save()
    except IntegrityError as exc:
        raise VehicleError("This vehicle already has an active loan.") from exc
    # bulk_create sends no save signals, on purpose: the loan itself is the audited record
    LoanInstallment.objects.bulk_create([
        LoanInstallment(
            loan=loan, company_id=loan.company_id, number=n, due_on=due_on, amount=amount,
            **({"status": Paid, "paid_on": due_on, "paid_amount": amount, "reference": "Paid before the system"}
               if n <= already_paid else {}))
        for n, due_on, amount in schedule])
    if vehicle.ownership != Vehicle.Ownership.LOAN:
        vehicle.ownership = Vehicle.Ownership.LOAN
        vehicle.save(update_fields=["ownership"])
    _complete_if_paid(loan)
    return loan


def _complete_if_paid(loan):
    if loan.status == VehicleLoan.Status.ACTIVE and not loan.installments.filter(status=Due).exists():
        loan.status = VehicleLoan.Status.COMPLETED
        loan.save()


@transaction.atomic
def pay_installment(installment, paid_on, amount, reference=""):
    installment = LoanInstallment.objects.select_for_update().get(pk=installment.pk)
    loan = VehicleLoan.objects.select_for_update().get(pk=installment.loan_id)
    if loan.is_voided or loan.status == VehicleLoan.Status.SETTLED:
        raise VehicleError("This loan is closed, so no payment can be recorded.")
    if installment.status != Due:
        raise VehicleError("This installment is already paid.")
    if paid_on > timezone.localdate():
        raise VehicleError("The payment date cannot be in the future.")
    if amount <= 0:
        raise VehicleError("The amount paid must be more than zero.")
    installment.status, installment.paid_on, installment.paid_amount = Paid, paid_on, amount
    installment.reference = (reference or "").strip()
    installment.save()
    _complete_if_paid(loan)
    return installment


@transaction.atomic
def reverse_payment(installment, reason):
    """Undo a payment that was recorded by mistake."""
    reason = (reason or "").strip()
    if not reason:
        raise VehicleError("Give a reason for undoing this payment.")
    installment = LoanInstallment.objects.select_for_update().get(pk=installment.pk)
    loan = VehicleLoan.objects.select_for_update().get(pk=installment.loan_id)
    if loan.is_voided or loan.status == VehicleLoan.Status.SETTLED:
        raise VehicleError("This loan is closed, so its payments cannot be changed.")
    if installment.status != Paid:
        raise VehicleError("This installment has no payment to undo.")
    installment.status, installment.paid_on, installment.paid_amount = Due, None, None
    installment.reference = f"Payment undone: {reason}"[:60]
    installment.save()
    if loan.status == VehicleLoan.Status.COMPLETED:
        loan.status = VehicleLoan.Status.ACTIVE
        loan.save()
    return installment


@transaction.atomic
def settle_early(loan, settled_on, amount, reference=""):
    """The bank accepted a settlement figure: every installment still due is cleared and the loan closes."""
    loan = VehicleLoan.objects.select_for_update().get(pk=loan.pk)
    if loan.is_voided or loan.status != VehicleLoan.Status.ACTIVE:
        raise VehicleError("Only an active loan can be settled.")
    if settled_on > timezone.localdate():
        raise VehicleError("The settlement date cannot be in the future.")
    if amount <= 0:
        raise VehicleError("The settlement amount must be more than zero.")
    if not loan.installments.filter(status=Due).exists():
        raise VehicleError("Nothing is left to settle.")
    loan.installments.filter(status=Due).update(status=Settled)      # no per-row audit: the loan records the settlement
    loan.status, loan.settled_on = VehicleLoan.Status.SETTLED, settled_on
    loan.settlement_amount, loan.settlement_reference = amount, (reference or "").strip()
    loan.save()
    return loan


@transaction.atomic
def void_loan(loan, reason):
    """Cancel a loan entered by mistake. Only possible while no payment is recorded against it."""
    reason = (reason or "").strip()
    if not reason:
        raise VehicleError("Give a reason for cancelling this loan.")
    loan = VehicleLoan.objects.select_for_update().get(pk=loan.pk)
    if loan.is_voided:
        raise VehicleError("This loan is already cancelled.")
    if loan.status == VehicleLoan.Status.SETTLED or loan.installments.filter(status=Paid).exists():
        raise VehicleError("Payments are recorded against this loan. Undo them first, or use early settlement.")
    loan.is_voided, loan.voided_reason = True, reason
    loan.save()
    return loan


# ---------------------------------------------------------------- alerts

def recipients(company_id, escalate):
    """Finance users of the company, plus Management once an installment is overdue."""
    User = get_user_model()
    groups = ["Finance"] + (["Management"] if escalate else [])
    users = User.objects.filter(is_active=True, groups__name__in=groups,
                                company_access__company_id=company_id).distinct()
    return sorted({u.email for u in users if u.email})


def _link(vehicle):
    try:
        path = reverse("web:vehicle_loan", args=[vehicle.pk])
    except NoReverseMatch:
        return ""
    return getattr(settings, "HRMS_BASE_URL", "").rstrip("/") + path


def build_message(installment, left):
    loan, v = installment.loan, installment.loan.vehicle
    what = f"installment {installment.number} of {loan.installment_count} on {v.plate_number} ({loan.lender})"
    if left < 0:
        subject = f"[Vehicles] OVERDUE: {what}, due {installment.due_on:%d %b %Y}"
        when = f"was due on {installment.due_on:%d %b %Y} ({-left} day{'s' if left != -1 else ''} ago) and is not recorded as paid"
    elif left == 0:
        subject = f"[Vehicles] Due today: {what}"
        when = "is due today"
    else:
        subject = f"[Vehicles] Due in {left} day{'s' if left != 1 else ''}: {what}"
        when = f"is due on {installment.due_on:%d %b %Y}"
    lines = [f"The {what} {when}.", "", f"Amount: {installment.amount:.3f} BHD"]
    if loan.account_no:
        lines.append(f"Loan number: {loan.account_no}")
    link = _link(v)
    if link:
        lines += ["", f"Open in Ivhrms: {link}"]
    lines += ["", "This is an automatic reminder from Ivhrms."]
    return subject, "\n".join(lines)


def run_loan_scan(today=None):
    """Email Finance about installments that fall due within a week, and once more if one is overdue. Safe to re-run."""
    today = today or timezone.localdate()
    stats = {"checked": 0, "alerts": 0, "emails": 0, "errors": 0}
    rows = (LoanInstallment.objects.filter(status=Due, loan__status=VehicleLoan.Status.ACTIVE, loan__is_voided=False,
                                           loan__vehicle__company__is_active=True)
            .select_related("loan", "loan__vehicle"))
    for inst in rows:
        left = (inst.due_on - today).days
        if left > ALERT_DAYS:
            continue
        stats["checked"] += 1
        state = "expired" if left < 0 else "due"
        if inst.alerts.filter(state=state).exists():
            continue
        try:
            with transaction.atomic():
                addresses = recipients(inst.company_id, escalate=state == "expired")
                sent = 0
                if addresses:
                    subject, body = build_message(inst, left)
                    sent = send_mass_mail([(subject, body, None, [a]) for a in addresses])
                LoanAlertLog.objects.create(installment=inst, state=state, recipients=sent)
            stats["alerts"] += 1
            stats["emails"] += sent
        except Exception:                           # one failing installment must not stop the others
            logger.exception("Loan alert failed for installment %s", inst.pk)
            stats["errors"] += 1                    # nothing was logged, so the next run retries it
    return stats
