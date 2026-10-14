"""Direct labor in a final settlement: who is eligible, the last month's wages and the monthly wage the other dues use.

The last month's pay is worked out by the labor payroll rules (so it agrees with a labor run), over the part of the month
up to the last working day. The other dues (leave, notice, gratuity) need one monthly wage, taken from the pay terms in
force on the last day: a monthly salary as it is; a daily rate times the days in a month (30); an hourly rate times the
standard hours times the days in a month. There are no allowances for labor, so the gratuity wage is the same figure."""
from decimal import Decimal
from types import SimpleNamespace

from apps.employees.models import Employee

from . import labor_payroll
from .calculation import money
from .models import PaymentDetail
from .services import PayrollError



def check_eligible(employee):
    """Staff are always eligible. Labor must be direct labor with a labor profile (contractor-supplied labor is paid
    through the contractor)."""
    if employee.worker_type == Employee.WorkerType.STAFF:
        return
    profile = getattr(employee, "labor_profile", None)
    if profile is None:
        raise PayrollError("This labor worker has no labor profile, so no pay terms. Add the profile under Labor first.")
    if profile.engagement != "direct":
        raise PayrollError("Contractor-supplied labor is paid through the contractor, not through payroll.")


def basis(employee, last_day, policy_days):
    """The monthly wage the dues are worked on, from the pay terms in force on the last day, or None."""
    rate = labor_payroll._rate_on(list(employee.labor_profile.rates.all()), last_day)
    if rate is None:
        return None
    days = Decimal(policy_days)
    if rate.wage_basis == labor_payroll.Basis.MONTHLY:
        wage, note = rate.rate, f"monthly salary {rate.rate:.3f}"
    elif rate.wage_basis == labor_payroll.Basis.DAILY:
        wage, note = rate.rate * days, f"daily rate {rate.rate:.3f} x {policy_days} days"
    else:
        wage = rate.rate * rate.standard_hours * days
        note = f"hourly rate {rate.rate:.3f} x {rate.standard_hours:.2f} standard hours x {policy_days} days"
    wage = money(wage)
    return SimpleNamespace(basic=wage, gratuity_wage=wage, total=wage, note=f"Monthly wage from the pay terms: {note}.")


def wages_part(case, emp, policy, month_start, last, month_days, kinds):
    """(items, issues, details) for the last month of a labor worker, by the labor payroll rules."""
    run = SimpleNamespace(period_start=month_start, period_end=last, company_id=emp.company_id,
                          policy_values=policy.values(), month_days=month_days)
    inputs = labor_payroll.gather(run, [emp])
    pay = PaymentDetail.objects.filter(employee=emp).first()
    fields = labor_payroll.build_line(emp, inputs, run, (pay.method, pay.bank_name, pay.iban) if pay else None)
    first_day = max(month_start, emp.joining_date)
    b = fields["breakdown"]
    items = [(kinds.WAGES, f"Wages {first_day:%d %b} to {last:%d %b %Y}", Decimal(b["wages"]))]
    if Decimal(b["overtime"]):
        items.append((kinds.WAGES, f"Approved overtime and premium ({Decimal(b['overtime_hours']):.2f} hours)",
                      Decimal(b["overtime"])))
    if fields["sio_employee"]:
        items.append((kinds.SIO, "Social insurance (SIO), employee share", -fields["sio_employee"]))
    details = {"month_days": month_days, "policy_days": policy.days_in_month,
               "wages": {"segments": [], "lost_days": b["lost_days"]}, "labor": {
                   "days_worked": b["days_worked"], "hours": b["hours"], "wages_by_kind": b["wages_by_kind"]}}
    return items, list(fields["issues"]), details
