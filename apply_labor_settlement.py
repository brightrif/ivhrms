"""Direct labor final settlements: apply the small edits to files already installed, and copy the new files.

Run from the project folder (where manage.py is):   python /path/to/labor_settlement/apply_labor_settlement.py

Each edit finds its place by exact text, keeps the file's own line endings, is skipped if already done, and if the
text is not found it changes nothing and prints what to add by hand. It never overwrites a file you changed."""
import sys
from pathlib import Path

HERE, ROOT = Path(__file__).resolve().parent, Path.cwd()

EDITS = [
    ('apps/payroll/settlements.py',
     'from .services import PayrollError, salary_on\n',
     'from .services import PayrollError, salary_on\nfrom . import settlement_labor\n'),
    ('apps/payroll/settlements.py',
     '    if employee.worker_type != Employee.WorkerType.STAFF:\n        raise PayrollError("A final settlement is for staff for now. Direct labor will follow.")\n',
     '    settlement_labor.check_eligible(employee)\n'),
    ('apps/payroll/settlements.py',
     '    records = list(SalaryRecord.objects.filter(employee=emp, effective_from__lte=last)\n                   .filter(Q(effective_to__isnull=True) | Q(effective_to__gte=month_start))\n                   .prefetch_related("allowances__allowance_type").order_by("-effective_from"))\n    attendance = dict(Attendance.objects.filter(employee=emp, date__range=(month_start, last)).values_list("date", "status"))\n    pay = PaymentDetail.objects.filter(employee=emp).first()\n    fields = calculation.compute_line(\n        joining_date=emp.joining_date, period_start=month_start, period_end=last, days_in_month=policy.days_in_month,\n        records=records, attendance=attendance, day_kinds=day_types(emp, month_start, last),\n        today=timezone.localdate(), nationality_text=emp.nationality, policy=policy.values(),\n        payment=(pay.method, pay.bank_name, pay.iban) if pay else None, month_days=month_days)\n    issues, items, details = list(fields["issues"]), [], {"month_days": month_days, "policy_days": policy.days_in_month}\n    first_day = max(month_start, emp.joining_date)\n    items.append((Kind.WAGES, f"Salary {first_day:%d %b} to {last:%d %b %Y}", fields["gross"]))\n    if fields["absence_deduction"]:\n        items.append((Kind.ABSENCE, f"Absence and unpaid days ({fields[\'unpaid_days\']:.2f} days)", -fields["absence_deduction"]))\n    if fields["sio_employee"]:\n        items.append((Kind.SIO, "Social insurance (SIO), employee share", -fields["sio_employee"]))\n    details["wages"] = {"segments": fields["breakdown"]["segments"], "lost_days": fields["breakdown"]["lost_days"]}\n\n',
     '    labor = emp.worker_type == Employee.WorkerType.LABOR\n    if labor:\n        items, issues, details = settlement_labor.wages_part(case, emp, policy, month_start, last, month_days, Kind)\n    else:\n        records = list(SalaryRecord.objects.filter(employee=emp, effective_from__lte=last)\n                       .filter(Q(effective_to__isnull=True) | Q(effective_to__gte=month_start))\n                       .prefetch_related("allowances__allowance_type").order_by("-effective_from"))\n        attendance = dict(Attendance.objects.filter(employee=emp, date__range=(month_start, last)).values_list("date", "status"))\n        pay = PaymentDetail.objects.filter(employee=emp).first()\n        fields = calculation.compute_line(\n            joining_date=emp.joining_date, period_start=month_start, period_end=last, days_in_month=policy.days_in_month,\n            records=records, attendance=attendance, day_kinds=day_types(emp, month_start, last),\n            today=timezone.localdate(), nationality_text=emp.nationality, policy=policy.values(),\n            payment=(pay.method, pay.bank_name, pay.iban) if pay else None, month_days=month_days)\n        issues, items, details = list(fields["issues"]), [], {"month_days": month_days, "policy_days": policy.days_in_month}\n        first_day = max(month_start, emp.joining_date)\n        items.append((Kind.WAGES, f"Salary {first_day:%d %b} to {last:%d %b %Y}", fields["gross"]))\n        if fields["absence_deduction"]:\n            items.append((Kind.ABSENCE, f"Absence and unpaid days ({fields[\'unpaid_days\']:.2f} days)", -fields["absence_deduction"]))\n        if fields["sio_employee"]:\n            items.append((Kind.SIO, "Social insurance (SIO), employee share", -fields["sio_employee"]))\n        details["wages"] = {"segments": fields["breakdown"]["segments"], "lost_days": fields["breakdown"]["lost_days"]}\n\n'),
    ('apps/payroll/settlements.py',
     '    record = salary_on(emp, last)\n    if record is None:',
     '    record = settlement_labor.basis(emp, last, policy.days_in_month) if labor else salary_on(emp, last)\n    if record is None:'),
    ('apps/payroll/settlements.py',
     '    details["basis"] = {k: str(v) for k, v in _wage_basis(record).items()}\n',
     '    details["basis"] = {k: str(v) for k, v in _wage_basis(record).items()}\n    if getattr(record, "note", ""):\n        details["basis_note"] = record.note\n'),
    ('apps/web/templates/web/payroll/settlement.html',
     'One day = {{ details.daily_rate }} BHD (monthly total &divide; {{ details.policy_days }}).</div>{% endif %}',
     'One day = {{ details.daily_rate }} BHD (monthly total &divide; {{ details.policy_days }}).</div>{% endif %}\n    {% if details.basis_note %}<div class="mb-2 text-body-secondary">{{ details.basis_note }}</div>{% endif %}'),
    ('apps/web/forms/payroll_settlements.py',
     'from apps.payroll.finalsettlement import FinalSettlement\n',
     'from apps.payroll.finalsettlement import FinalSettlement\nfrom apps.payroll.services import in_payroll\n'),
    ('apps/web/forms/payroll_settlements.py',
     '            self.fields["employee"].queryset = (Employee.objects.filter(company__in=companies, worker_type="staff")\n                                                .exclude(pk__in=list(live)).order_by("employee_no"))',
     '            self.fields["employee"].queryset = (in_payroll(user, include_separated=True)\n                                                .exclude(pk__in=list(live)).order_by("employee_no"))'),
    ('apps/payroll/labor_payroll.py',
     'from .calculation import issue, money\n',
     'from .calculation import issue, money\nfrom .settlement_rules import SCHEME_START, eosg_rate\n'),
    ('apps/payroll/labor_payroll.py',
     'def labor_people(run):',
     'def _settled(run):\n    """People whose final settlement pays this month (and later): they must not be in the run as well."""\n    from . import settlements\n    return settlements.settled_employee_ids(run.company_id, run.period_end)\n\n\ndef labor_people(run):'),
    ('apps/payroll/labor_payroll.py',
     '.exclude(status=Employee.Status.SEPARATED).select_related("labor_profile")',
     '.exclude(status=Employee.Status.SEPARATED).exclude(pk__in=_settled(run)).select_related("labor_profile")'),
    ('apps/payroll/labor_payroll.py',
     'def worker_wages(emp, inputs, first, last):',
     'def worker_wages(emp, inputs, first, last, month_days=None):'),
    ('apps/payroll/labor_payroll.py',
     '    n_days = (last - first).days + 1\n    parts = {"daily"',
     '    n_days = month_days or ((last - first).days + 1)         # a part month still earns by the whole month\n    parts = {"daily"'),
    ('apps/payroll/labor_payroll.py',
     '    wages_raw, parts = worker_wages(emp, inputs, first, last)',
     '    wages_raw, parts = worker_wages(emp, inputs, first, last, getattr(run, "month_days", None))'),
    ('apps/payroll/labor_payroll.py',
     '    issues += sio_issues\n',
     '    issues += sio_issues\n    eosg = None                       # the company\'s monthly payment to the SIO for a non-Bahraini\'s end-of-service gratuity\n    if kind == nationality.OTHER and not nationality.is_gcc(emp.nationality) and wages > 0 and last >= SCHEME_START:\n        rate = eosg_rate(emp.joining_date, last)\n        eosg = {"rate": str(rate), "wage": str(wages), "amount": str(money(wages * rate / HUNDRED))}\n'),
    ('apps/payroll/labor_payroll.py',
     '            "segments": [], "missing_days": [],\n',
     '            "segments": [], "missing_days": [], "eosg": eosg,\n'),
    ('apps/payroll/locks.py',
     '    return f"{line.run.label} ({line.run.company.code})" if line else None\n',
     '    if line:\n        return f"{line.run.label} ({line.run.company.code})"\n    from datetime import date\n\n    from .finalsettlement import FinalSettlement\n\n    cases = FinalSettlement.objects.filter(\n        employee_id__in=list(employee_ids), status__in=[FinalSettlement.Status.APPROVED, FinalSettlement.Status.PAID],\n        last_working_day__gte=first).order_by("last_working_day")\n    for case in cases:\n        if last is None or date(case.last_working_day.year, case.last_working_day.month, 1) <= last:\n            return f"{case.last_working_day:%B %Y} (final settlement)"\n    return None\n'),
    ('apps/payroll/test_settlements.py',
     '    def test_direct_labor_is_not_covered_yet(self):\n        from .tests import make_profile\n        worker = Employee.objects.create(company=self.co, employee_no="L-1", first_name="Ravi", worker_type="labor",\n                                         joining_date=date(2020, 1, 1))\n        make_profile(worker, "direct")\n        with self.assertRaisesMessage(PayrollError, "Direct labor will follow"):\n            self.settle(worker)\n',
     '    def test_labor_needs_a_profile_and_must_not_be_contractor_supplied(self):\n        from .tests import make_profile\n        orphan = Employee.objects.create(company=self.co, employee_no="L-1", first_name="Orphan", worker_type="labor",\n                                         joining_date=date(2020, 1, 1))\n        with self.assertRaisesMessage(PayrollError, "no labor profile"):\n            self.settle(orphan)\n        contracted = Employee.objects.create(company=self.co, employee_no="L-2", first_name="Contracted",\n                                             worker_type="labor", joining_date=date(2020, 1, 1))\n        make_profile(contracted, "contracted")\n        with self.assertRaisesMessage(PayrollError, "paid through the contractor"):\n            self.settle(contracted)\n'),
]


def main():
    if not (ROOT / "manage.py").exists():
        sys.exit(f"Run this from the Ivhrms project folder (the one with manage.py). You are in: {ROOT}")
    done, skipped, manual = [], [], []
    for rel, old, new in EDITS:
        path = ROOT / rel
        raw = path.read_bytes() if path.exists() else None
        if raw is None:
            manual.append((rel, old, new))
            continue
        crlf = b"\r\n" in raw
        text = raw.decode("utf-8").replace("\r\n", "\n")
        if new in text:
            skipped.append(rel)
        elif text.count(old) == 1:
            text = text.replace(old, new, 1)
            path.write_bytes((text.replace("\n", "\r\n") if crlf else text).encode("utf-8"))
            done.append(rel)
        else:
            manual.append((rel, old, new))
    crlf_project = b"\r\n" in (ROOT / "manage.py").read_bytes()
    copied, beside = [], []
    for src in sorted((HERE / "files").rglob("*")):
        if not src.is_file():
            continue
        rel = src.relative_to(HERE / "files")
        text = src.read_text(encoding="utf-8").replace("\r\n", "\n")
        data = (text.replace("\n", "\r\n") if crlf_project else text).encode("utf-8")
        target = ROOT / rel
        if target.exists():
            if target.read_bytes().replace(b"\r\n", b"\n") == data.replace(b"\r\n", b"\n"):
                skipped.append(str(rel))
                continue
            target = target.with_name(target.name + ".new")
            beside.append(str(target.relative_to(ROOT)))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        copied.append(str(rel))
    print(f"Edited: {len(done)}   New files: {len(copied)}   Already in place: {len(skipped)}")
    for name in done:
        print("  ~", name)
    for name in copied:
        print("  +", name)
    for name in beside:
        print("  ! you had changed this file, so the new version was saved beside it:", name)
    for rel, old, new in manual:
        print(f"\nDO BY HAND in {rel}: find\n{old}\nand replace with\n{new}")


if __name__ == "__main__":
    main()
