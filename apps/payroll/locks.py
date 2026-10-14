"""Closing a month for labor once its payroll is approved.

The labor module asks, before it changes pay terms, hours or overtime, whether anyone has frozen that period. This is
the answer payroll gives: an approved or paid labor run covering the dates, for any of the workers, closes them."""
from .payrun import PayrollLine, PayrollRun


def labor_period_closed(employee_ids, first, last):
    """The label of an approved or paid labor run that covers any of these dates for any of these workers, or None.
    `last` may be None for 'from `first` onwards'."""
    lines = PayrollLine.objects.filter(
        employee_id__in=list(employee_ids), run__kind=PayrollRun.Kind.LABOR,
        run__status__in=[PayrollRun.Status.APPROVED, PayrollRun.Status.PAID], run__period_end__gte=first)
    if last is not None:
        lines = lines.filter(run__period_start__lte=last)
    line = lines.select_related("run__company").order_by("run__period_start").first()
    if line:
        return f"{line.run.label} ({line.run.company.code})"
    from datetime import date

    from .finalsettlement import FinalSettlement

    cases = FinalSettlement.objects.filter(
        employee_id__in=list(employee_ids), status__in=[FinalSettlement.Status.APPROVED, FinalSettlement.Status.PAID],
        last_working_day__gte=first).order_by("last_working_day")
    for case in cases:
        if last is None or date(case.last_working_day.year, case.last_working_day.month, 1) <= last:
            return f"{case.last_working_day:%B %Y} (final settlement)"
    return None
