from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.crypto import get_random_string

from .models import Employee, EmploymentRecord
from .signals import employee_onboarded

TRACKED = ("company", "employment_type", "department", "designation", "grade", "location", "reporting_manager")
_PASSWORD_CHARS = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # no look-alike characters


class EmployeeError(Exception):
    pass


@transaction.atomic
def record_assignment(employee, *, effective_from, reason, remarks="", **changes):
    """Close the open record, open a new one, and sync the Employee's current fields."""
    unknown = set(changes) - set(TRACKED)
    if unknown:
        raise ValueError(f"Unknown fields: {unknown}")

    current = employee.history.filter(effective_to__isnull=True).first()
    if current and effective_from <= current.effective_from:
        raise ValueError("Effective date must be after the current record's start date.")
    if current:
        current.effective_to = effective_from - timedelta(days=1)
        current.save()

    values = {f: getattr(employee, f) for f in TRACKED}
    values.update(changes)
    record = EmploymentRecord.objects.create(
        employee=employee, effective_from=effective_from, reason=reason, remarks=remarks, **values)

    if effective_from <= timezone.localdate():     # future-dated rows are applied later by a daily job
        for field, value in values.items():
            setattr(employee, field, value)
        employee.save()
    return record


def assignment_on(employee, on_date):
    """The department/grade/manager/etc. that applied on a given date."""
    return (employee.history
            .filter(effective_from__lte=on_date)
            .filter(Q(effective_to__isnull=True) | Q(effective_to__gte=on_date))
            .first())


# ---------------------------------------------------------------- logins

def _temporary_password():
    return get_random_string(12, _PASSWORD_CHARS)


def default_username(employee):
    no = employee.employee_no.lower().replace(" ", "")
    code = employee.company.code.lower()
    return no if no.startswith(code) else f"{code}-{no}"


@transaction.atomic
def create_login(employee, *, username=None):
    """Create a web login with a temporary password. Returns {'username', 'password'}; show it once."""
    User = get_user_model()
    if employee.user_id:
        raise EmployeeError("This employee already has a login.")
    if employee.status == Employee.Status.SEPARATED:
        raise EmployeeError("This employee has left the company.")
    username = (username or default_username(employee)).strip()
    if User.objects.filter(username__iexact=username).exists():
        raise EmployeeError(f"The username '{username}' is already taken.")
    password = _temporary_password()
    user = User(username=username, email=employee.email, first_name=employee.first_name,
                last_name=employee.last_name, must_change_password=True)
    user.set_password(password)
    user.save()
    employee.user = user
    employee.save(update_fields=["user"])
    return {"username": user.username, "password": password}


@transaction.atomic
def reset_password(employee):
    if not employee.user_id:
        raise EmployeeError("This employee has no login yet.")
    user, password = employee.user, _temporary_password()
    user.set_password(password)
    user.must_change_password = True
    user.save()
    return {"username": user.username, "password": password}


# ---------------------------------------------------------------- onboarding

@transaction.atomic
def create_employee(employee, *, shift=None, with_login=False, username=None):
    """Save a new (unsaved) Employee, open its history, and let other apps react.
    Returns (employee, credentials or None). Any failure rolls the whole creation back."""
    if employee.pk:
        raise EmployeeError("This employee has already been saved.")
    employee.save()
    record_assignment(employee, effective_from=employee.joining_date,
                      reason=EmploymentRecord.Reason.JOINING)
    credentials = create_login(employee, username=username) if with_login else None
    employee_onboarded.send(sender=Employee, employee=employee, shift=shift)
    return employee, credentials