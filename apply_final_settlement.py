"""Final settlement: apply the small edits to files already installed, and copy the new files.

Run from the project folder (where manage.py is):   python /path/to/final_settlement/apply_final_settlement.py

Each edit finds its place by exact text, keeps the file's own line endings, is skipped if already done, and if the
text is not found it changes nothing and prints what to add by hand. It never overwrites a file you changed."""
import sys
from pathlib import Path

HERE, ROOT = Path(__file__).resolve().parent, Path.cwd()

EDITS = [
    ('apps/payroll/models.py',
     '    objects = CompanyQuerySet.as_manager()\n\n    class Meta:\n        ordering = ["company", "name"]',
     '    counts_for_gratuity = models.BooleanField(\n        "Social allowance (counts for gratuity)", default=False,\n        help_text="Tick for the social allowance, if the company pays one. Gratuity is worked out on basic salary plus "\n                  "this allowance only.")\n\n    objects = CompanyQuerySet.as_manager()\n\n    class Meta:\n        ordering = ["company", "name"]'),
    ('apps/payroll/models.py',
     '    def __str__(self):\n        return f"{self.employee} from {self.effective_from}"',
     '    @property\n    def gratuity_wage(self):\n        """Basic plus the allowances marked as the social allowance: the wage end-of-service gratuity is worked on."""\n        return self.basic + sum((a.amount for a in self.allowances.all() if a.allowance_type.counts_for_gratuity),\n                                Decimal("0"))\n\n    def __str__(self):\n        return f"{self.employee} from {self.effective_from}"'),
    ('apps/payroll/models.py',
     'from . import payrun  # noqa: E402,F401  (registers the payroll run models)',
     'from . import payrun  # noqa: E402,F401  (registers the payroll run models)\nfrom . import finalsettlement  # noqa: E402,F401  (registers the final settlement models)'),
    ('apps/payroll/nationality.py',
     'def classify(text):',
     '_GCC = {_norm(t) for t in ("Saudi", "Saudi Arabian", "Saudi Arabia", "Omani", "Oman", "Emirati", "UAE", "United Arab Emirates",\n                           "Kuwaiti", "Kuwait", "Qatari", "Qatar")}\n\n\ndef is_gcc(text):\n    """True for a GCC national other than Bahraini. They fall under the unified GCC insurance scheme, so the standard\n    gratuity rules are not applied to them without a decision."""\n    return _norm(text) in _GCC\n\n\ndef classify(text):'),
    ('apps/payroll/calculation.py',
     'from . import nationality\n',
     'from . import nationality\nfrom .settlement_rules import SCHEME_START, eosg_rate\n'),
    ('apps/payroll/calculation.py',
     'def compute_line(*, joining_date, period_start, period_end, days_in_month, records, attendance, day_kinds, today,\n                 nationality_text, policy, payment):',
     'def compute_line(*, joining_date, period_start, period_end, days_in_month, records, attendance, day_kinds, today,\n                 nationality_text, policy, payment, month_days=None):'),
    ('apps/payroll/calculation.py',
     '    n_days = (period_end - period_start).days + 1\n',
     '    n_days = month_days or ((period_end - period_start).days + 1)      # a part month still earns by the whole month\n'),
    ('apps/payroll/calculation.py',
     '    gross = sio_base = absence = sio_lost = unpaid_days = ZERO\n',
     '    gross = sio_base = absence = sio_lost = unpaid_days = eosg_base = ZERO\n'),
    ('apps/payroll/calculation.py',
     '                sio_base += sio / n_days\n',
     '                sio_base += sio / n_days\n                eosg_base += Decimal(getattr(record, "gratuity_wage", 0)) / n_days\n'),
    ('apps/payroll/calculation.py',
     '    method, bank, iban = payment if payment else ("", "", "")\n',
     '    eosg = None                       # the company\'s monthly payment to the SIO for a non-Bahraini\'s end-of-service gratuity\n    if (kind == nationality.OTHER and not nationality.is_gcc(nationality_text) and gross > 0\n            and period_end >= SCHEME_START):\n        rate = eosg_rate(joining_date, period_end)\n        eosg = {"rate": str(rate), "wage": str(money(eosg_base)), "amount": str(money(eosg_base * rate / HUNDRED))}\n\n    method, bank, iban = payment if payment else ("", "", "")\n'),
    ('apps/payroll/calculation.py',
     '            "lost_days": lost_days, "missing_days": [d.isoformat() for d in missing]},',
     '            "lost_days": lost_days, "missing_days": [d.isoformat() for d in missing], "eosg": eosg},'),
    ('apps/payroll/runs.py',
     'from . import calculation, labor_payroll\n',
     'from . import calculation, labor_payroll, settlements\n'),
    ('apps/payroll/runs.py',
     '    return list(Employee.objects.filter(company=run.company_id, worker_type=Employee.WorkerType.STAFF,\n                                        joining_date__lte=run.period_end)\n                .exclude(status=Employee.Status.SEPARATED).order_by("employee_no", "first_name"))',
     '    settled = settlements.settled_employee_ids(run.company_id, run.period_end)      # their final settlement pays them\n    return list(Employee.objects.filter(company=run.company_id, worker_type=Employee.WorkerType.STAFF,\n                                        joining_date__lte=run.period_end)\n                .exclude(status=Employee.Status.SEPARATED).exclude(pk__in=settled).order_by("employee_no", "first_name"))'),
    ('apps/payroll/runs.py',
     '    employee = getattr(user, "employee", None)\n    if employee is None:\n        raise PayrollError("Your login is not linked to an employee record, so the approval cannot be sent in your "\n                           "name. Ask the system administrator to link it.")',
     '    settled = settlements.settled_employee_ids(run.company_id, run.period_end) & set(run.lines.values_list("employee_id", flat=True))\n    if settled:\n        raise PayrollError(f"{len(settled)} {\'person has\' if len(settled) == 1 else \'people have\'} a final settlement that pays "\n                           "this month, so they must not be in the run. Press Recalculate.")\n    employee = getattr(user, "employee", None)\n    if employee is None:\n        raise PayrollError("Your login is not linked to an employee record, so the approval cannot be sent in your "\n                           "name. Ask the system administrator to link it.")'),
    ('apps/payroll/runs.py',
     '    taken = set(PayrollAdjustment.objects.filter(source_id__isnull=False).exclude(run__status=Status.CANCELLED)\n                .values_list("source", "source_id"))',
     '    from .finalsettlement import SettlementItem\n\n    taken = set(PayrollAdjustment.objects.filter(source_id__isnull=False).exclude(run__status=Status.CANCELLED)\n                .values_list("source", "source_id"))\n    taken |= set(SettlementItem.objects.filter(source_id__isnull=False).exclude(settlement__status="cancelled")\n                 .values_list("source", "source_id"))'),
    ('apps/payroll/runs.py',
     '            "cash": sum((line.net for line in lines if line.payment_method == "cash"), ZERO)}',
     '            "cash": sum((line.net for line in lines if line.payment_method == "cash"), ZERO),\n            "eosg": sum((Decimal((line.breakdown.get("eosg") or {}).get("amount", "0")) for line in lines), ZERO)}'),
    ('apps/payroll/defaults.py',
     '    if ApprovalFlow.objects.using(using).filter(code=FLOW_CODE, company__isnull=True).exists():\n        return\n    group, _ = Group.objects.using(using).get_or_create(name="Management")\n    # bulk_create sends no save signals, on purpose: this is system data, not something a person did\n    flow = ApprovalFlow.objects.using(using).bulk_create([ApprovalFlow(code=FLOW_CODE, name="Payroll approval")])[0]\n    ApprovalStep.objects.using(using).bulk_create([ApprovalStep(\n        flow=flow, order=1, name="Management approval", approver_type=ApprovalStep.ApproverType.GROUP, group=group)])',
     '    group, _ = Group.objects.using(using).get_or_create(name="Management")\n    for code, name in ((FLOW_CODE, "Payroll approval"), ("payroll.settlement", "Final settlement approval")):\n        if ApprovalFlow.objects.using(using).filter(code=code, company__isnull=True).exists():\n            continue\n        # bulk_create sends no save signals, on purpose: this is system data, not something a person did\n        flow = ApprovalFlow.objects.using(using).bulk_create([ApprovalFlow(code=code, name=name)])[0]\n        ApprovalStep.objects.using(using).bulk_create([ApprovalStep(\n            flow=flow, order=1, name="Management approval", approver_type=ApprovalStep.ApproverType.GROUP, group=group)])'),
    ('apps/payroll/signals.py',
     '    from . import runs\n    if approval.flow.code == runs.FLOW_CODE:\n        runs.finalize(approval)',
     '    from . import runs, settlements\n    if approval.flow.code == runs.FLOW_CODE:\n        runs.finalize(approval)\n    elif approval.flow.code == settlements.FLOW_CODE:\n        settlements.finalize(approval)'),
    ('apps/payroll/settings_spec.py',
     '        Capability("See the payroll policy", "payroll.view_payrollpolicy"),',
     '        Capability("See final settlements", "payroll.view_finalsettlement",\n                   "Shows what a leaver is owed. Give it together with the right to see payroll runs, which shows the Payroll menu."),\n        Capability("Prepare final settlements", "payroll.add_finalsettlement",\n                   "Start a settlement, recalculate it, add lines, pick up charges, cancel a draft."),\n        Capability("Send a final settlement for approval", "payroll.submit_finalsettlement",\n                   "Management then decides it in the Approvals inbox. Approval marks the person as Separated."),\n        Capability("Record final settlement payments", "payroll.pay_finalsettlement"),\n        Capability("See the payroll policy", "payroll.view_payrollpolicy"),'),
    ('apps/payroll/services.py',
     'def create_allowance_type(company, *, name, counts_for_sio=False):',
     'def create_allowance_type(company, *, name, counts_for_sio=False, counts_for_gratuity=False):'),
    ('apps/payroll/services.py',
     '    return AllowanceType.objects.create(company=company, name=name, counts_for_sio=bool(counts_for_sio))',
     '    return AllowanceType.objects.create(company=company, name=name, counts_for_sio=bool(counts_for_sio),\n                                        counts_for_gratuity=bool(counts_for_gratuity))'),
    ('apps/payroll/services.py',
     'def update_allowance_type(allowance, *, name, counts_for_sio):',
     'def update_allowance_type(allowance, *, name, counts_for_sio, counts_for_gratuity=None):'),
    ('apps/payroll/services.py',
     '    allowance.name, allowance.counts_for_sio = name, bool(counts_for_sio)\n',
     '    allowance.name, allowance.counts_for_sio = name, bool(counts_for_sio)\n    if counts_for_gratuity is not None:\n        allowance.counts_for_gratuity = bool(counts_for_gratuity)\n'),
    ('apps/web/forms/payroll.py',
     '    def __init__(self, *args, user, allowance=None, **kwargs):',
     '    counts_for_gratuity = forms.BooleanField(\n        required=False, label="Social allowance (counts for gratuity)",\n        help_text="Tick for the social allowance, if the company pays one. Gratuity is worked out on basic salary plus "\n                  "this allowance only.")\n\n    def __init__(self, *args, user, allowance=None, **kwargs):'),
    ('apps/web/forms/payroll.py',
     '                                counts_for_sio=allowance.counts_for_sio)',
     '                                counts_for_sio=allowance.counts_for_sio,\n                                counts_for_gratuity=allowance.counts_for_gratuity)'),
    ('apps/web/views/payroll.py',
     '"rows": [["company"], ["name"], ["counts_for_sio"]]}],',
     '"rows": [["company"], ["name"], ["counts_for_sio"], ["counts_for_gratuity"]]}],'),
    ('apps/web/views/payroll.py',
     'services.create_allowance_type(cd["company"], name=cd["name"], counts_for_sio=cd["counts_for_sio"])',
     'services.create_allowance_type(cd["company"], name=cd["name"], counts_for_sio=cd["counts_for_sio"],\n                                           counts_for_gratuity=cd["counts_for_gratuity"])'),
    ('apps/web/views/payroll.py',
     'services.update_allowance_type(allowance, name=cd["name"], counts_for_sio=cd["counts_for_sio"])',
     'services.update_allowance_type(allowance, name=cd["name"], counts_for_sio=cd["counts_for_sio"],\n                                           counts_for_gratuity=cd["counts_for_gratuity"])'),
    ('apps/web/templates/web/payroll/allowances.html',
     '<th>SIO wage</th>',
     '<th>SIO wage</th><th>Social allowance</th>'),
    ('apps/web/templates/web/payroll/allowances.html',
     '<td>{% if t.counts_for_sio %}<span class="badge text-bg-info">Counts</span>{% else %}<span class="text-body-secondary small">No</span>{% endif %}</td>',
     '<td>{% if t.counts_for_sio %}<span class="badge text-bg-info">Counts</span>{% else %}<span class="text-body-secondary small">No</span>{% endif %}</td>\n        <td>{% if t.counts_for_gratuity %}<span class="badge text-bg-info">Yes</span>{% else %}<span class="text-body-secondary small">No</span>{% endif %}</td>'),
    ('apps/web/templates/web/payroll/allowances.html',
     '<td colspan="6" class="text-center text-body-secondary py-4">No allowance types yet.',
     '<td colspan="7" class="text-center text-body-secondary py-4">No allowance types yet.'),
    ('apps/web/urls/payroll.py',
     'from apps.web.views import payroll, payroll_runs',
     'from apps.web.views import payroll, payroll_runs, payroll_settlements'),
    ('apps/web/urls/payroll.py',
     '    path("payroll/runs/<int:pk>/payslips/", payroll_runs.payslips, name="payroll_payslips"),',
     '    path("payroll/runs/<int:pk>/payslips/", payroll_runs.payslips, name="payroll_payslips"),\n    path("payroll/settlements/", payroll_settlements.settlement_list, name="payroll_settlements"),\n    path("payroll/settlements/new/", payroll_settlements.settlement_create, name="payroll_settlement_create"),\n    path("payroll/settlements/<int:pk>/", payroll_settlements.settlement_detail, name="payroll_settlement"),\n    path("payroll/settlements/<int:pk>/edit/", payroll_settlements.settlement_edit, name="payroll_settlement_edit"),\n    path("payroll/settlements/<int:pk>/recalculate/", payroll_settlements.settlement_recalculate, name="payroll_settlement_recalculate"),\n    path("payroll/settlements/<int:pk>/items/new/", payroll_settlements.item_add, name="payroll_settlement_item_add"),\n    path("payroll/settlements/<int:pk>/items/<int:item_pk>/remove/", payroll_settlements.item_remove, name="payroll_settlement_item_remove"),\n    path("payroll/settlements/<int:pk>/charges/", payroll_settlements.settlement_charges, name="payroll_settlement_charges"),\n    path("payroll/settlements/<int:pk>/submit/", payroll_settlements.settlement_submit, name="payroll_settlement_submit"),\n    path("payroll/settlements/<int:pk>/withdraw/", payroll_settlements.settlement_withdraw, name="payroll_settlement_withdraw"),\n    path("payroll/settlements/<int:pk>/cancel/", payroll_settlements.settlement_cancel, name="payroll_settlement_cancel"),\n    path("payroll/settlements/<int:pk>/pay/", payroll_settlements.settlement_pay, name="payroll_settlement_pay"),\n    path("payroll/settlements/<int:pk>/statement/", payroll_settlements.settlement_statement, name="payroll_settlement_statement"),\n    path("payroll/settlements/<int:pk>/certificate/", payroll_settlements.settlement_certificate, name="payroll_settlement_certificate"),'),
    ('apps/web/templates/web/payroll/_tabs.html',
     '  {% if perms.payroll.view_allowancetype %}<li class="nav-item">',
     '  {% if perms.payroll.view_finalsettlement %}<li class="nav-item"><a class="nav-link{% if tab == \'settlements\' %} active{% endif %}" href="{% url \'web:payroll_settlements\' %}">Final settlements</a></li>{% endif %}\n  {% if perms.payroll.view_allowancetype %}<li class="nav-item">'),
    ('apps/web/templates/web/payroll/run.html',
     '&middot; company SIO cost on top {{ totals.sio_employer|floatformat:3 }} BHD</div>',
     '&middot; company SIO cost on top {{ totals.sio_employer|floatformat:3 }} BHD &middot; SIO gratuity contribution to pay the SIO {{ totals.eosg|floatformat:3 }} BHD</div>'),
    ('apps/web/templates/web/payroll/line.html',
     'Company SIO cost on top: {{ line.sio_employer|floatformat:3 }} BHD. Paid by:',
     'Company SIO cost on top: {{ line.sio_employer|floatformat:3 }} BHD.{% if line.breakdown.eosg %} SIO gratuity contribution to pay the SIO: {{ line.breakdown.eosg.amount|floatformat:3 }} BHD ({{ line.breakdown.eosg.rate }}% of {{ line.breakdown.eosg.wage|floatformat:3 }}).{% endif %} Paid by:'),
    ('apps/payroll/outputs.py',
     'def register(run):',
     'def _eosg(line):\n    return Decimal((line.breakdown.get("eosg") or {}).get("amount", "0"))\n\n\ndef register(run):'),
    ('apps/payroll/outputs.py',
     '"SIO (employer)", "Paid by", "Paid on"]',
     '"SIO (employer)", "Paid by", "Paid on", "SIO gratuity (company)"]'),
    ('apps/payroll/outputs.py',
     'Cell(line.sio_employer, xlsx.MONEY), line.get_payment_label(), line.paid_on])',
     'Cell(line.sio_employer, xlsx.MONEY), line.get_payment_label(), line.paid_on,\n                     Cell(_eosg(line), xlsx.MONEY)])'),
    ('apps/payroll/outputs.py',
     'widths=[10, 28, 7, 12, 12, 14, 12, 12, 12, 14, 14, 12]',
     'widths=[10, 28, 7, 12, 12, 14, 12, 12, 12, 14, 14, 12, 16]'),
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
