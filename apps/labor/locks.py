"""Lets another module close a period for labor workers, so pay that has been approved cannot quietly change.

A module registers a check with `register()`. Labor calls `ensure_open()` just before it changes pay terms, hours or
overtime; if any check says the period is closed, nothing is changed and the person is told why. Payroll registers
the check that closes a month once its labor payroll run is approved."""
from .services import LaborError

_checks = []


def register(check):
    """`check(employee_ids, first, last)` returns the label of the closed period (say "March 2026"), or None."""
    if check not in _checks:
        _checks.append(check)


def ensure_open(employee_ids, first, last, action):
    """Raise LaborError if the dates are closed for any of these workers. `last=None` means 'from `first` onwards'."""
    ids = list(employee_ids)
    if not ids:
        return
    for check in _checks:
        label = check(ids, first, last)
        if label:
            raise LaborError(f"Payroll for {label} has already been approved, so {action} is not allowed. "
                             "Add an adjustment to the next payroll run for any difference.")
