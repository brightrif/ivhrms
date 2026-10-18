"""Who may have a NEW family visa.

Family status is what lets HR record family details and ask for a new family visa. The rule, in this order:
  1. the decision on the person: "approved" (the manager approved a family status request) or "removed" (HR or
     Management took it away, with a reason);
  2. otherwise the default of the person's designation (ticked under Organization > Designations);
  3. otherwise no family status.

Nobody has it until someone decides, on purpose. A renewal or a cancellation never asks (see
visa_services.blocked_reason)."""
from dataclasses import dataclass

from django.db import transaction

from .sponsor import FamilyVisaException

Mode = FamilyVisaException.Mode


class EligibilityError(Exception):
    pass


@dataclass(frozen=True)
class SponsorStatus:
    allowed: bool
    source: str            # "exception", "designation" or "default"
    text: str              # short, for badges and the employee page
    reason: str = ""       # the reason given with an exception


def sponsor_status(employee):
    row = FamilyVisaException.objects.filter(employee=employee).first()
    if row is not None and row.mode == Mode.ALLOWED:
        return SponsorStatus(True, "exception", "Family status approved", row.reason)
    if row is not None and row.mode == Mode.NOT_ALLOWED:
        return SponsorStatus(False, "exception", "Family status removed", row.reason)
    designation = employee.designation
    if designation is not None and designation.family_visa_default:
        return SponsorStatus(True, "designation", f"Family status as {designation.name}")
    return SponsorStatus(False, "default", "No family status")


def sponsor_problem(employee):
    """Why this employee cannot have a new family visa, in plain words, or None when they can."""
    status = sponsor_status(employee)
    if status.allowed:
        return None
    if status.source == "exception":
        return (f"{employee.full_name}'s family status was removed ({status.reason}). "
                "It can be requested again from the employee page.")
    return (f"{employee.full_name} does not have family status yet. Request it first (Staff, Family & Visas), "
            "or tick the designation under Organization.")


@transaction.atomic
def set_exception(employee, mode, reason=""):
    """Allow or refuse the family visa option for one person, or put them back on the designation's default.
    Requests already made are never touched. Returns the row, or None when there was nothing to change."""
    if mode not in Mode.values:
        raise EligibilityError("Choose Default, Allowed or Not allowed.")
    reason = " ".join((reason or "").split())
    if mode != Mode.DEFAULT and not reason:
        raise EligibilityError("Give the reason.")
    row = FamilyVisaException.objects.select_for_update().filter(employee=employee).first()
    if row is None:
        if mode == Mode.DEFAULT:
            return None
        row = FamilyVisaException(employee=employee)
    elif (row.mode, row.reason) == (mode, reason):
        return row
    row.mode, row.reason = mode, reason
    row.save()
    return row
