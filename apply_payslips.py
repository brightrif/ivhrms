"""Payslips: apply the small edits to files already installed, and copy the two new files.

Run from the project folder (where manage.py is):   python /path/to/payslips/apply_payslips.py

Each edit finds its place by exact text, keeps the file's own line endings, is skipped if already done, and if the
text is not found it changes nothing and prints what to add by hand. It never overwrites a file you changed."""
import sys
from pathlib import Path

HERE, ROOT = Path(__file__).resolve().parent, Path.cwd()

EDITS = [
    ('apps/payroll/calculation.py',
     'def record_on(records, day):',
     'def _parts(record):\n    """The salary\'s make-up, kept on the line so a payslip can list the basic salary and each allowance."""\n    basic = getattr(record, "basic", None)\n    if basic is None:\n        return None\n    return {"basic": str(basic), "allowances": [[a.allowance_type.name, str(a.amount)] for a in record.allowances.all()]}\n\n\ndef record_on(records, day):'),
    ('apps/payroll/calculation.py',
     'segments.append({"id": record.pk, "from": day, "to": day, "days": 1, "total": total, "sio": sio})',
     'segments.append({"id": record.pk, "from": day, "to": day, "days": 1, "total": total, "sio": sio,\n                                     "parts": _parts(record)})'),
    ('apps/payroll/calculation.py',
     '"monthly_total": str(s["total"]), "amount": str(money(s["total"] * s["days"] / n_days))}',
     '"monthly_total": str(s["total"]), "amount": str(money(s["total"] * s["days"] / n_days)),\n                          "parts": s["parts"]}'),
    ('apps/payroll/payrun.py',
     '    def get_payment_label(self):',
     '    @property\n    def iban_masked(self):\n        """The IBAN with the middle hidden, for a payslip: BH67 **** 3456."""\n        return f"{self.iban[:4]} \\u2022\\u2022\\u2022\\u2022 {self.iban[-4:]}" if len(self.iban) > 8 else self.iban\n\n    @property\n    def total_earnings(self):\n        return self.gross + self.additions\n\n    @property\n    def total_deductions(self):\n        return self.absence_deduction + self.sio_employee + self.deductions\n\n    def get_payment_label(self):'),
    ('apps/web/views/payroll_runs.py',
     '@hr_perm("payroll.view_payrollrun")\ndef run_export(request, pk, kind):',
     'def _issue_payslips(request, run, lines):\n    """Show payslips ready to print. Only for a run that is approved or paid; every view is audited."""\n    from apps.audit import services as audit\n\n    adjustments = {}\n    for a in run.adjustments.all():\n        adjustments.setdefault(a.employee_id, []).append(a)\n    for line in lines:\n        audit.log("download", line, module="payroll", subject_employee_id=line.employee_id, company_id=run.company_id,\n                  changes={"payslip": run.label})\n    return render(request, "web/payroll/payslip.html", {\n        "run": run, "slips": [{"line": l, "adjustments": adjustments.get(l.employee_id, [])} for l in lines]})\n\n\n@hr_perm("payroll.view_payrollrun")\ndef payslips(request, pk):\n    run = _run(request, pk)\n    if not run.frozen:\n        messages.error(request, "Payslips are issued once the run is approved.")\n        return redirect(_url(run))\n    return _issue_payslips(request, run, list(run.lines.all()))\n\n\n@hr_perm("payroll.view_payrollrun")\ndef payslip(request, pk, line_pk):\n    run = _run(request, pk)\n    if not run.frozen:\n        messages.error(request, "Payslips are issued once the run is approved.")\n        return redirect(_url(run))\n    return _issue_payslips(request, run, [get_object_or_404(PayrollLine, pk=line_pk, run=run)])\n\n\n@hr_perm("payroll.view_payrollrun")\ndef run_export(request, pk, kind):'),
    ('apps/web/urls/payroll.py',
     '    path("payroll/runs/<int:pk>/export/<str:kind>/", payroll_runs.run_export, name="payroll_run_export"),',
     '    path("payroll/runs/<int:pk>/export/<str:kind>/", payroll_runs.run_export, name="payroll_run_export"),\n    path("payroll/runs/<int:pk>/payslips/", payroll_runs.payslips, name="payroll_payslips"),\n    path("payroll/runs/<int:pk>/lines/<int:line_pk>/payslip/", payroll_runs.payslip, name="payroll_payslip"),'),
    ('apps/web/templates/web/payroll/run.html',
     '<i class="bi bi-download me-1"></i>Excel</button>',
     '<i class="bi bi-download me-1"></i>Downloads</button>'),
    ('apps/web/templates/web/payroll/run.html',
     '<li><a class="dropdown-item" href="{% url \'web:payroll_run_export\' run.pk \'cash\' %}">Cash pay list</a></li>',
     '<li><a class="dropdown-item" href="{% url \'web:payroll_run_export\' run.pk \'cash\' %}">Cash pay list</a></li>\n        {% if run.status == \'approved\' or run.status == \'paid\' %}<li><hr class="dropdown-divider"></li>\n        <li><a class="dropdown-item" target="_blank" href="{% url \'web:payroll_payslips\' run.pk %}"><i class="bi bi-printer me-2"></i>All payslips (print or save as PDF)</a></li>{% endif %}'),
    ('apps/web/templates/web/payroll/line.html',
     '<a class="btn btn-outline-secondary" href="{% url \'web:payroll_employee\' line.employee_id %}">Salary and payment</a>',
     '<a class="btn btn-outline-secondary" href="{% url \'web:payroll_employee\' line.employee_id %}">Salary and payment</a>\n    {% if run.status == \'approved\' or run.status == \'paid\' %}<a class="btn btn-outline-primary" target="_blank" href="{% url \'web:payroll_payslip\' run.pk line.pk %}"><i class="bi bi-printer me-1"></i>Payslip</a>{% endif %}'),
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
