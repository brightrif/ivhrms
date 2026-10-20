"""Loans and salary advances: create, approve, pay out, deduct in payroll, repay early, reschedule, recover at exit.

Flow: Draft (HR prepares) > Waiting for approval (Management, in the Approvals inbox) > Approved > Active once the bank
transfer is recorded > Closed when the balance is zero. A loan agreed verbally skips the approval: HR records who agreed
and when, and it starts as Approved. A rejected or withdrawn loan goes back to Draft. Views call these; they never change
the tables directly."""
from datetime import date
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.utils import timezone

from apps.core import approvals
from apps.core.models import ApprovalAction, ApprovalRequest
from apps.employees.models import Employee

from . import loan_settings
from .calculation import issue
from .loans import FLOW_CODE, Loan, LoanInstalment, LoanPayment
from .payrun import PayrollAdjustment
from .services import PayrollError, salary_on

Status, Kind = Loan.Status, Loan.Kind
Inst, Method = LoanInstalment.Status, LoanPayment.Method
ZERO = Decimal("0")
THOU = Decimal("0.001")


class LimitError(PayrollError):
    """The loan passes a limit set in Settings. HR may go on if they give the reason."""

    def __init__(self, warnings):
        self.warnings = warnings
        super().__init__(" ".join(warnings) + " Give the reason to go ahead.")


# ------------------------------------------------------------------ small helpers

def money(value):
    return Decimal(value).quantize(THOU, ROUND_HALF_UP)


def first_of(d):
    return d.replace(day=1)


def add_months(d, n):
    year, month = divmod(d.year * 12 + d.month - 1 + n, 12)
    return date(year, month + 1, 1)


def interest_for(amount, rate, months):
    return money(Decimal(amount) * Decimal(rate) / 100 * months / 12)


def make_schedule(total, months, first_month):
    """[(month, amount)]: equal instalments rounded down to the fils; the last one takes the rounding."""
    per = (Decimal(total) / months).quantize(THOU, ROUND_DOWN)
    rows = [(add_months(first_month, i), per) for i in range(months - 1)]
    rows.append((add_months(first_month, months - 1), Decimal(total) - per * (months - 1)))
    return rows


def _locked(loan):
    return Loan.objects.select_for_update().select_related("employee", "company").get(pk=loan.pk)


def _need(loan, status, what):
    if loan.status != status:
        raise PayrollError(f"This loan is {loan.get_status_display().lower()}, so it cannot be {what}.")


def _taken_in_a_run(instalment):
    return (PayrollAdjustment.objects.filter(source="loan", source_id=instalment.pk)
            .exclude(run__status="cancelled").select_related("run").first())


# ------------------------------------------------------------------ creating and changing a draft

def limit_warnings(employee, amount, months):
    limits = loan_settings.limits_for(employee.company)
    if not any(limits.values()):
        return []
    out, per = [], money(Decimal(amount) / months)
    salary = salary_on(employee, timezone.localdate())
    total = getattr(salary, "total", None)
    if limits["months"] and months > limits["months"]:
        out.append(f"{months} instalments is more than the {limits['months']} allowed.")
    if (limits["salaries"] or limits["share"]) and not total:
        out.append("There is no salary on record to compare the limits with.")
    elif total:
        if limits["salaries"] and amount > total * limits["salaries"]:
            out.append(f"{amount:,.3f} BHD is more than {limits['salaries']:g} monthly salaries ({total * limits['salaries']:,.3f}).")
        if limits["share"] and per > total * limits["share"] / 100:
            out.append(f"An instalment of {per:,.3f} BHD is more than {limits['share']}% of the monthly pay ({total:,.3f}).")
    return out


def _check_employee(employee):
    from .finalsettlement import FinalSettlement

    if employee.status == Employee.Status.SEPARATED:
        raise PayrollError(f"{employee.full_name} has left the company.")
    if FinalSettlement.objects.filter(employee=employee).exclude(status="cancelled").exists():
        raise PayrollError(f"{employee.full_name} has a final settlement, so no new loan can be made.")


def _validated(employee, *, kind, amount, months, first_month, interest_rate, override_reason):
    if kind not in Kind.values:
        raise PayrollError("Choose a loan or a salary advance.")
    try:
        amount = Decimal(amount)
    except Exception:
        raise PayrollError("The amount must be a number.") from None
    if amount <= 0 or amount != money(amount):
        raise PayrollError("The amount must be more than zero, with at most three decimal places.")
    try:
        months = 1 if kind == Kind.ADVANCE else int(months or 0)
    except (TypeError, ValueError):
        raise PayrollError("The number of instalments must be a whole number.") from None
    if not 1 <= months <= 120:
        raise PayrollError("The number of instalments must be between 1 and 120.")
    first_month = first_of(first_month)
    if first_month < first_of(employee.joining_date):
        raise PayrollError("The first deduction cannot be before the month the person joined.")
    rate = loan_settings.default_interest(employee.company) if interest_rate in (None, "") else Decimal(interest_rate)
    if not 0 <= rate <= 100:
        raise PayrollError("The interest must be between 0 and 100 percent.")
    warnings = limit_warnings(employee, amount, months)
    if warnings and not (override_reason or "").strip():
        raise LimitError(warnings)
    return {"kind": kind, "amount": amount, "months": months, "first_month": first_month, "interest_rate": rate,
            "interest_total": interest_for(amount, rate, months), "override_reason": (override_reason or "").strip()[:255]}


def _make_instalments(loan):
    loan.instalments.all().delete()
    for month, amount in make_schedule(loan.total, loan.months, loan.first_month):
        LoanInstalment.objects.create(loan=loan, due_month=month, amount=amount, original_amount=amount)


def _approve(loan):
    _make_instalments(loan)
    loan.status, loan.approved_at, loan.note = Status.APPROVED, timezone.now(), ""
    loan.save()


@transaction.atomic
def create_loan(user, employee, *, kind, amount, months, first_month, interest_rate=None, reason="", verbal=False,
                agreed_by="", agreed_on=None, override_reason=""):
    """A draft loan; or, if `verbal`, an approved one (HR records who agreed it and when)."""
    employee = Employee.objects.select_for_update().select_related("company").get(pk=employee.pk)
    _check_employee(employee)
    values = _validated(employee, kind=kind, amount=amount, months=months, first_month=first_month,
                        interest_rate=interest_rate, override_reason=override_reason)
    agreed_by = " ".join((agreed_by or "").split())
    if verbal:
        if not agreed_by or not agreed_on:
            raise PayrollError("Say who agreed it and on what date.")
        if agreed_on > timezone.localdate():
            raise PayrollError("The date it was agreed cannot be in the future.")
    loan = Loan.objects.create(employee=employee, prepared_by=user, reason=(reason or "").strip()[:255],
                               agreed_by=agreed_by if verbal else "", agreed_on=agreed_on if verbal else None, **values)
    if verbal:
        _approve(loan)
    return loan


@transaction.atomic
def update_draft(user, loan, *, kind, amount, months, first_month, interest_rate=None, reason="", override_reason=""):
    loan = _locked(loan)
    _need(loan, Status.DRAFT, "changed")
    values = _validated(loan.employee, kind=kind, amount=amount, months=months, first_month=first_month,
                        interest_rate=interest_rate, override_reason=override_reason)
    for key, value in values.items():
        setattr(loan, key, value)
    loan.reason = (reason or "").strip()[:255]
    loan.save()
    return loan


def preview(amount, months, first_month, rate):
    """The schedule a draft would get, for the page."""
    total = Decimal(amount) + interest_for(amount, rate, months)
    return make_schedule(total, months, first_of(first_month))


@transaction.atomic
def cancel_loan(user, loan, reason=""):
    loan = _locked(loan)
    if loan.status not in (Status.DRAFT, Status.APPROVED):
        raise PayrollError(f"This loan is {loan.get_status_display().lower()}, so it cannot be cancelled. "
                           "Take it back from approval first, or waive the balance if it is active.")
    if loan.status == Status.APPROVED and not (reason or "").strip():
        raise PayrollError("Give the reason for cancelling an approved loan.")
    loan.status, loan.closed_on, loan.closed_note = Status.CANCELLED, timezone.localdate(), (reason or "").strip()[:255]
    loan.save()
    return loan


# ------------------------------------------------------------------ approval

@transaction.atomic
def submit_loan(loan, user):
    loan = _locked(loan)
    _need(loan, Status.DRAFT, "sent for approval")
    _check_employee(loan.employee)
    try:
        approvals.submit(FLOW_CODE, loan, employee=loan.employee, requested_by=user)
    except approvals.ApprovalError as exc:
        raise PayrollError(str(exc)) from exc
    loan.status, loan.note = Status.SUBMITTED, ""
    loan.save()
    return loan


@transaction.atomic
def withdraw_loan(loan, user):
    loan = _locked(loan)
    if loan.status != Status.SUBMITTED:
        raise PayrollError("This loan is not waiting for approval.")
    ct = ContentType.objects.get_for_model(Loan)
    approval = ApprovalRequest.objects.filter(content_type=ct, object_id=str(loan.pk),
                                              status=ApprovalRequest.Status.PENDING).first()
    if approval is None:
        raise PayrollError("This loan is not waiting for approval.")
    try:
        approvals.cancel(approval.pk, user)
    except approvals.ApprovalError as exc:
        raise PayrollError(str(exc)) from exc
    return Loan.objects.get(pk=loan.pk)


@transaction.atomic
def finalize(approval):
    """Called when the approval finishes, whatever the outcome. Idempotent. Approval makes the schedule."""
    loan = Loan.objects.select_for_update().select_related("employee").get(pk=approval.object_id)
    if loan.status != Status.SUBMITTED:
        return loan
    if approval.status == ApprovalRequest.Status.APPROVED:
        _approve(loan)
    else:
        last = approval.actions.filter(decision=ApprovalAction.Decision.REJECT).order_by("-acted_at").first()
        loan.status = Status.DRAFT
        loan.note = (f"Rejected: {last.comment}" if last and last.comment else
                     "Rejected." if approval.status == ApprovalRequest.Status.REJECTED else "Taken back for changes.")
        loan.save()
    return loan


# ------------------------------------------------------------------ paying out, repaying

@transaction.atomic
def record_payout(user, loan, paid_on, reference):
    loan = _locked(loan)
    _need(loan, Status.APPROVED, "paid out")
    reference = " ".join((reference or "").split())
    if not reference:
        raise PayrollError("Give the bank transfer reference.")
    if paid_on > timezone.localdate():
        raise PayrollError("The payout date cannot be in the future.")
    loan.status, loan.paid_out_on, loan.payout_reference = Status.ACTIVE, paid_on, reference[:60]
    loan.save()
    return loan


def _close_if_paid(loan, on):
    if loan.balance <= 0 and loan.status == Status.ACTIVE:
        loan.status, loan.closed_on, loan.closed_note = Status.CLOSED, on, loan.closed_note or "Fully repaid."
        loan.save()


def _cover(loan, amount):
    """An amount paid outside payroll shortens the schedule from the far end: the last instalments are covered first.
    An instalment already waiting in a payroll run is left alone."""
    left = Decimal(amount)
    for row in loan.instalments.select_for_update().filter(status=Inst.SCHEDULED).order_by("-due_month", "-id"):
        if left <= 0:
            break
        if _taken_in_a_run(row):
            continue
        if left >= row.amount:
            left -= row.amount
            row.status = Inst.COVERED
        else:
            row.amount -= left
            left = ZERO
        row.save()


@transaction.atomic
def record_payment(user, loan, amount, paid_on, method, reference="", note=""):
    """Repaid outside payroll: a part payment, or the whole balance (a pre-closure)."""
    loan = _locked(loan)
    _need(loan, Status.ACTIVE, "repaid")
    if method not in (Method.BANK, Method.CASH):
        raise PayrollError("Choose bank transfer or cash.")
    try:
        amount = money(amount)
    except Exception:
        raise PayrollError("The amount must be a number.") from None
    if amount <= 0:
        raise PayrollError("The amount must be more than zero.")
    if amount > loan.balance:
        raise PayrollError(f"The balance is only {loan.balance:,.3f} BHD.")
    if paid_on > timezone.localdate():
        raise PayrollError("The payment date cannot be in the future.")
    if loan.paid_out_on and paid_on < loan.paid_out_on:
        raise PayrollError(f"It cannot be before the payout ({loan.paid_out_on:%d %b %Y}).")
    LoanPayment.objects.create(loan=loan, paid_on=paid_on, amount=amount, method=method, recorded_by=user,
                               reference=(reference or "").strip()[:80], note=(note or "").strip()[:255])
    _cover(loan, amount)
    _close_if_paid(loan, paid_on)
    return loan


def pre_close(user, loan, paid_on, method, reference=""):
    return record_payment(user, loan, Loan.objects.get(pk=loan.pk).balance, paid_on, method, reference,
                          "Paid off early")


@transaction.atomic
def waive(user, loan, reason):
    loan = _locked(loan)
    _need(loan, Status.ACTIVE, "waived")
    reason = " ".join((reason or "").split())
    if not reason:
        raise PayrollError("Give the reason for waiving the balance.")
    balance = loan.balance
    if balance > 0:
        LoanPayment.objects.create(loan=loan, paid_on=timezone.localdate(), amount=balance, method=Method.WAIVED,
                                   note=reason[:255], recorded_by=user)
    loan.instalments.filter(status=Inst.SCHEDULED).update(status=Inst.COVERED)
    loan.status, loan.closed_on, loan.closed_note = Status.CLOSED, timezone.localdate(), f"Waived: {reason}"[:255]
    loan.save()
    return loan


# ------------------------------------------------------------------ changing the schedule

@transaction.atomic
def skip_next(user, loan):
    """Leave out the next instalment; the same amount is added after the last one."""
    loan = _locked(loan)
    _need(loan, Status.ACTIVE, "changed")
    row = loan.instalments.select_for_update().filter(status=Inst.SCHEDULED).order_by("due_month", "id").first()
    if row is None:
        raise PayrollError("There is no instalment left to skip.")
    run = _taken_in_a_run(row)
    if run:
        raise PayrollError(f"That instalment is already in the {run.run.label} payroll run. Remove it there first.")
    last = loan.instalments.exclude(status__in=[Inst.REPLACED]).order_by("-due_month", "-id").first()
    row.status = Inst.SKIPPED
    row.save()
    LoanInstalment.objects.create(loan=loan, due_month=add_months(last.due_month, 1), amount=row.amount,
                                  original_amount=row.original_amount)
    return loan


@transaction.atomic
def reschedule(user, loan, months, first_month):
    """New instalments for what is still owed."""
    loan = _locked(loan)
    _need(loan, Status.ACTIVE, "rescheduled")
    try:
        months = int(months or 0)
    except (TypeError, ValueError):
        raise PayrollError("The number of instalments must be a whole number.") from None
    if not 1 <= months <= 120:
        raise PayrollError("The number of instalments must be between 1 and 120.")
    rows = list(loan.instalments.select_for_update().filter(status=Inst.SCHEDULED))
    if not rows:
        raise PayrollError("There is nothing left to reschedule.")
    for row in rows:
        run = _taken_in_a_run(row)
        if run:
            raise PayrollError(f"An instalment is already in the {run.run.label} payroll run. Remove it there first.")
    balance = loan.balance
    if balance <= 0:
        raise PayrollError("There is nothing left to reschedule.")
    LoanInstalment.objects.filter(pk__in=[r.pk for r in rows]).update(status=Inst.REPLACED)
    for month, amount in make_schedule(balance, months, first_of(first_month)):
        LoanInstalment.objects.create(loan=loan, due_month=month, amount=amount, original_amount=amount)
    return loan


# ------------------------------------------------------------------ payroll

def _label(instalment):
    loan = instalment.loan
    live = list(loan.instalments.exclude(status__in=[Inst.SKIPPED, Inst.REPLACED]).order_by("due_month", "id")
                .values_list("pk", flat=True))
    if loan.kind == Kind.ADVANCE:
        return f"Salary advance {loan.number}"
    n = live.index(instalment.pk) + 1 if instalment.pk in live else 0
    return f"Loan {loan.number}, instalment {n} of {len(live)}"


def loan_candidates(run, taken, employee_ids):
    """Instalments due by the end of the run's month that nobody has deducted yet, for the people in the run:
    [(source, id, employee_id, category, label, amount)], like the fines and renewal fees."""
    found = []
    rows = (LoanInstalment.objects.filter(status=Inst.SCHEDULED, loan__status=Status.ACTIVE,
                                          loan__employee_id__in=employee_ids, due_month__lte=run.period_end)
            .select_related("loan__employee").order_by("loan__employee__employee_no", "due_month", "id"))
    for row in rows:
        if ("loan", row.pk) not in taken and row.amount > 0:
            found.append(("loan", row.pk, row.loan.employee_id, PayrollAdjustment.Category.LOAN, _label(row), row.amount))
    return found


def mark_instalments_deducted(run):
    """Once a run is approved, the instalments it deducts count as repaid. Returns problems to show on the run."""
    problems, today = [], timezone.localdate()
    for adjustment in run.adjustments.filter(source="loan", source_id__isnull=False):
        row = (LoanInstalment.objects.select_for_update().select_related("loan")
               .filter(pk=adjustment.source_id).first())
        if row is None or (row.status == Inst.DEDUCTED and row.run_id == run.pk):
            continue
        if row.status != Inst.SCHEDULED:
            problems.append(f"{row.loan.number}: the instalment was {row.get_status_display().lower()} before this run "
                            "was approved, so it was not counted as repaid.")
            continue
        loan = Loan.objects.select_for_update().get(pk=row.loan_id)
        on = min(run.period_end, today)
        amount = min(adjustment.amount, loan.balance)
        row.status, row.run, row.deducted_on = Inst.DEDUCTED, run, on
        row.save()
        if amount > 0:
            LoanPayment.objects.create(loan=loan, instalment=row, paid_on=on, amount=amount, method=Method.PAYROLL,
                                       reference=run.label)
        _close_if_paid(loan, on)
    return problems


# ------------------------------------------------------------------ final settlement

def settlement_loan_lines(case):
    """The leaver's open loans as system lines of the settlement, oldest first. What the settlement can pay is taken;
    anything left stays on the loan and is shown. Returns the messages for the case."""
    from .finalsettlement import SettlementItem

    loans = list(Loan.objects.filter(employee_id=case.employee_id, status=Status.ACTIVE).order_by("id"))
    messages = []
    available = max(ZERO, sum((i.amount for i in case.items.all()), ZERO))
    for n, loan in enumerate(loans):
        balance = loan.balance
        take = min(balance, available)
        available -= take
        if take > 0:
            SettlementItem.objects.create(settlement=case, kind=SettlementItem.Kind.LOAN, is_system=True,
                                          label=f"Loan {loan.number} balance", amount=-take, source="loanbal",
                                          source_id=loan.pk, position=90 + n)
        if take < balance:
            messages.append(issue("loan_owed", "warn", f"Loan {loan.number}: {balance - take:,.3f} BHD is still owed after "
                                  "this settlement. It stays on the loan. Open the loan to waive it, or to agree how it "
                                  "will be repaid."))
    return messages


def recover_settlement_loans(case):
    """On approval, what the settlement takes counts as repaid. Returns problems to show on the case."""
    problems = []
    on = min(case.last_working_day, timezone.localdate())
    for item in case.items.filter(source="loanbal", source_id__isnull=False):
        loan = Loan.objects.select_for_update().filter(pk=item.source_id).first()
        if loan is None or loan.status != Status.ACTIVE:
            continue
        amount = min(-item.amount, loan.balance)
        if amount <= 0:
            continue
        LoanPayment.objects.create(loan=loan, paid_on=on, amount=amount, method=Method.SETTLEMENT,
                                   reference="Final settlement")
        _cover(loan, amount)
        if loan.balance <= 0:
            _close_if_paid(loan, on)
        else:
            loan.note = f"Left the company. {loan.balance:,.3f} BHD is still owed."[:500]
            loan.save()
    return problems
