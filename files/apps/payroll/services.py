"""The rules for staff pay and payment details. Views and forms call these; they never change the tables directly.

Who payroll covers: staff, and direct labor. Labor supplied by a contractor company is paid through the contractor's
statement, so payroll leaves it out entirely."""
import re
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Exists, OuterRef, Q

from apps.employees.models import Employee

from .models import AllowanceType, PaymentDetail, SalaryAllowance, SalaryRecord

CENT = Decimal("0.001")
STAFF, LABOR, SEPARATED = Employee.WorkerType.STAFF, Employee.WorkerType.LABOR, Employee.Status.SEPARATED


class PayrollError(Exception):
    """A rule was broken. The message is written to be shown to the person."""


# ------------------------------------------------------------------ who is covered

def in_payroll(user, *, include_separated=False):
    """Everyone the user may see whom payroll pays: staff and direct labor, never contractor-supplied labor."""
    people = Employee.objects.for_user(user).exclude(labor_profile__engagement="contracted")
    return people if include_separated else people.exclude(status=SEPARATED)


def with_coverage(people, today):
    """Mark each person with has_salary (a salary in force today) and has_payment (payment details filled in)."""
    in_force = (SalaryRecord.objects.filter(employee=OuterRef("pk"), effective_from__lte=today)
                .filter(Q(effective_to__isnull=True) | Q(effective_to__gte=today)))
    return people.annotate(has_salary=Exists(in_force),
                           has_payment=Exists(PaymentDetail.objects.filter(employee=OuterRef("pk"))))


def coverage(user, today):
    """The gaps HR has to close before payroll can run."""
    people = with_coverage(in_payroll(user), today)
    staff = people.filter(worker_type=STAFF)
    return {"total": people.count(), "staff_total": staff.count(),
            "staff_without_salary": staff.filter(has_salary=False).count(),
            "without_payment": people.filter(has_payment=False).count()}


# ------------------------------------------------------------------ allowance types

def _name(value):
    name = " ".join((value or "").split())
    if not name:
        raise PayrollError("Enter a name for the allowance.")
    return name


def _check_name_free(company, name, exclude_pk=None):
    taken = AllowanceType.objects.filter(company=company, name__iexact=name)
    if exclude_pk:
        taken = taken.exclude(pk=exclude_pk)
    if taken.exists():
        raise PayrollError(f"{company.name} already has an allowance called {name}.")


@transaction.atomic
def create_allowance_type(company, *, name, counts_for_sio=False):
    name = _name(name)
    _check_name_free(company, name)
    return AllowanceType.objects.create(company=company, name=name, counts_for_sio=bool(counts_for_sio))


@transaction.atomic
def update_allowance_type(allowance, *, name, counts_for_sio):
    allowance = AllowanceType.objects.select_for_update().get(pk=allowance.pk)
    name = _name(name)
    _check_name_free(allowance.company, name, exclude_pk=allowance.pk)
    allowance.name, allowance.counts_for_sio = name, bool(counts_for_sio)
    allowance.save()
    return allowance


def people_receiving(allowance):
    """How many people have this allowance on their latest salary."""
    return (SalaryAllowance.objects.filter(allowance_type=allowance, record__effective_to__isnull=True)
            .values("record__employee").distinct().count())


@transaction.atomic
def set_allowance_active(allowance, active):
    allowance = AllowanceType.objects.select_for_update().get(pk=allowance.pk)
    if not active:
        n = people_receiving(allowance)
        if n:
            raise PayrollError(f"{n} {'person is' if n == 1 else 'people are'} paid {allowance.name}. "
                               "Change their salaries first, then switch it off.")
    allowance.is_active = active
    allowance.save(update_fields=["is_active"])
    return allowance


# ------------------------------------------------------------------ salaries

def _money(value, what):
    try:
        amount = Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        raise PayrollError(f"{what} must be a number.")
    if amount <= 0:
        raise PayrollError(f"{what} must be more than zero.")
    if amount != amount.quantize(CENT):
        raise PayrollError(f"{what} can have at most three decimal places.")
    return amount


def _check_staff(employee, *, allow_separated=False):
    if employee.worker_type != STAFF:
        raise PayrollError("Salary records are for staff. Labor workers are paid from their pay terms under Labor.")
    if employee.status == SEPARATED and not allow_separated:
        raise PayrollError("This person has left the company.")


def _chosen_allowances(employee, allowances):
    """(AllowanceType, amount) pairs -> {type id: (type, amount)}. A blank amount means the allowance is not paid."""
    chosen = {}
    for allowance, amount in allowances:
        if amount in (None, ""):
            continue
        if allowance.company_id != employee.company_id:
            raise PayrollError(f"{allowance.name} belongs to another company.")
        if not allowance.is_active:
            raise PayrollError(f"{allowance.name} is switched off. Switch it on in Settings first.")
        chosen[allowance.pk] = (allowance, _money(amount, allowance.name))
    return chosen


@transaction.atomic
def change_salary(employee, *, effective_from, basic, allowances, reason, remarks=""):
    """Start a new salary on a date. The current one ends the day before, so history is never rewritten."""
    employee = Employee.objects.select_for_update().get(pk=employee.pk)
    _check_staff(employee)
    if reason not in SalaryRecord.Reason.values:
        raise PayrollError("Choose a reason for the change.")
    basic = _money(basic, "Basic salary")
    chosen = _chosen_allowances(employee, allowances)
    if effective_from < employee.joining_date:
        raise PayrollError(f"The salary cannot start before the joining date, {employee.joining_date:%d %b %Y}.")
    latest = SalaryRecord.objects.filter(employee=employee, effective_to__isnull=True).first()
    if latest and effective_from <= latest.effective_from:
        raise PayrollError(f"The new salary must start after the current one, which began on "
                           f"{latest.effective_from:%d %b %Y}. To fix a mistake in it, use Correct instead.")
    if latest:
        latest.effective_to = effective_from - timedelta(days=1)
        latest.save()
    record = SalaryRecord.objects.create(employee=employee, effective_from=effective_from, basic=basic,
                                         reason=reason, remarks=remarks or "")
    for allowance, amount in chosen.values():
        SalaryAllowance.objects.create(record=record, allowance_type=allowance, amount=amount)
    return record


@transaction.atomic
def correct_salary(record, *, basic, allowances, remarks):
    """Fix a mistake in the latest salary, in place. Only the latest can be corrected; earlier ones are history.
    The change is audited (the amounts are hidden there) and the reason is kept on the record.

    Phase 2 must refuse this once a payroll run has used the record."""
    record = SalaryRecord.objects.select_for_update().select_related("employee").get(pk=record.pk)
    if record.effective_to is not None:
        raise PayrollError("Only the latest salary can be corrected. Earlier ones are history; start a new salary instead.")
    employee = record.employee
    _check_staff(employee, allow_separated=True)
    if not (remarks or "").strip():
        raise PayrollError("Say what was wrong, so the history makes sense later.")
    record.basic = _money(basic, "Basic salary")
    chosen = _chosen_allowances(employee, allowances)
    record.remarks = " ".join(remarks.split())[:255]
    record.save()
    existing = {a.allowance_type_id: a for a in record.allowances.all()}
    for type_id, (allowance, amount) in chosen.items():
        row = existing.pop(type_id, None)
        if row is None:
            SalaryAllowance.objects.create(record=record, allowance_type=allowance, amount=amount)
        elif row.amount != amount:
            row.amount = amount
            row.save()
    for row in existing.values():
        row.delete()
    return record


def salary_history(employee):
    return (SalaryRecord.objects.filter(employee=employee)
            .prefetch_related("allowances__allowance_type").order_by("-effective_from"))


def salary_on(employee, on_date):
    """The salary in force on a date, or None."""
    return (salary_history(employee).filter(effective_from__lte=on_date)
            .filter(Q(effective_to__isnull=True) | Q(effective_to__gte=on_date)).first())


def upcoming_salary(employee, today):
    """A salary that starts after today, if one has been entered."""
    return salary_history(employee).filter(effective_from__gt=today).order_by("effective_from").first()


# ------------------------------------------------------------------ payment details

def clean_iban(value):
    """The IBAN without spaces, in capitals, once its check digits agree. Raises PayrollError if not."""
    iban = "".join((value or "").split()).upper()
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{11,30}", iban):
        raise PayrollError("That does not look like an IBAN. It starts with two letters and two digits, e.g. BH.. ")
    if iban.startswith("BH") and len(iban) != 22:
        raise PayrollError("A Bahrain IBAN has 22 characters.")
    digits = "".join(str(int(ch, 36)) for ch in iban[4:] + iban[:4])
    if int(digits) % 97 != 1:
        raise PayrollError("The IBAN check digits do not match. Please check it against the bank letter.")
    return iban


@transaction.atomic
def save_payment_details(employee, *, method, bank_name="", iban=""):
    """Record how someone is paid. Cash needs nothing more; a bank transfer needs the bank and a valid IBAN."""
    if getattr(employee, "labor_profile", None) is not None and employee.labor_profile.engagement == "contracted":
        raise PayrollError("Contractor-supplied labor is paid through the contractor, not through payroll.")
    if method not in PaymentDetail.Method.values:
        raise PayrollError("Choose bank transfer or cash.")
    bank_name = " ".join((bank_name or "").split())
    if method == PaymentDetail.Method.BANK:
        if not bank_name:
            raise PayrollError("Enter the bank name.")
        iban = clean_iban(iban)
    else:
        bank_name = iban = ""
    detail = PaymentDetail.objects.filter(employee=employee).first() or PaymentDetail(employee=employee)
    detail.method, detail.bank_name, detail.iban = method, bank_name, iban
    detail.save()
    return detail
