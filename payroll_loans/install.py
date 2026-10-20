#!/usr/bin/env python
"""Install Loans (loans and salary advances) into Ivhrms: Payroll > Loans.

    cd /c/Django/Projects/Ivhrms
    source ivenv/Scripts/activate
    python /path/to/payroll_loans/install.py        (or give the project folder as the first argument)

What it does, in order:
  1. copies the new files (a file that already exists and differs is saved as <name>.bak first)
  2. makes the small edits to existing files; each says ok, already, or MANUAL with exact instructions
  3. creates the migration (skip with --no-migrations)
Safe to run twice. Windows line endings are kept. Git Bash paths such as /c/Django/... are understood."""
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
FILES = HERE / "files"
manual_items = []


def fix_path(raw):
    m = re.match(r"^/([a-zA-Z])/(.*)$", raw)
    return f"{m.group(1).upper()}:/{m.group(2)}" if m and os.name == "nt" else raw


def read(path):
    raw = path.read_bytes().decode("utf-8")
    return raw.replace("\r\n", "\n"), "\r\n" in raw


def write(path, text, crlf):
    path.write_bytes((text.replace("\n", "\r\n") if crlf else text).encode("utf-8"))


def manual(label, rel, how):
    manual_items.append(label)
    print(f"  MANUAL  {label}\n          file: {rel}\n" + "\n".join("          " + line for line in how.splitlines()))


def copy_files(root):
    for src in sorted(FILES.rglob("*")):
        if not src.is_file() or src.suffix == ".pyc" or "__pycache__" in src.parts:
            continue
        rel = src.relative_to(FILES)
        dest = root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            if dest.read_bytes() == src.read_bytes():
                print(f"  already {rel.as_posix()}")
                continue
            shutil.copy2(dest, dest.with_name(dest.name + ".bak"))
            print(f"  ok      {rel.as_posix()}   (the old one is saved as {dest.name}.bak)")
        else:
            print(f"  ok      {rel.as_posix()}   (new)")
        shutil.copy2(src, dest)


def edit(root, rel, label, old, new, marker):
    path = root / rel
    if not path.exists():
        return manual(label, rel, f"The file was not found. Make this change by hand:\n  find:\n{old}\n  replace with:\n{new}")
    text, crlf = read(path)
    if marker in text:
        return print(f"  already {label}")
    if text.count(old) != 1:
        why = "the text to change was not found" if old not in text else "the text to change appears more than once"
        return manual(label, rel, f"({why}) Make this change by hand:\n  find:\n{old}\n  replace with:\n{new}")
    write(path, text.replace(old, new), crlf)
    print(f"  ok      {label}")


def append(root, rel, label, block, marker):
    path = root / rel
    if not path.exists():
        return manual(label, rel, f"The file was not found. Add this at the very end:\n{block}")
    text, crlf = read(path)
    if marker in text:
        return print(f"  already {label}")
    write(path, text.rstrip("\n") + "\n\n" + block.rstrip("\n") + "\n", crlf)
    print(f"  ok      {label}")


SIGNAL = '''@receiver(approval_completed)
def loan_decided(sender, approval, **kwargs):
    from . import loan_services
    if approval.flow.code == loan_services.FLOW_CODE:
        loan_services.finalize(approval)'''

SETTINGS_FUNCTION = '''def _loan_settings():
    from .loan_settings import loan_settings
    return loan_settings()


'''
SETTINGS_SECTION = '''            template="web/settings/_payroll_allowances.html", context=allowance_context),
        Section(
            title="Loans",
            intro="Loans and salary advances. The default interest is 0, and every limit is 0, which means no limit. "
                  "When a loan goes over a limit, HR gets a warning and must give a reason to go ahead.",
            settings=_loan_settings()),
    ],
    capabilities=['''
SETTINGS_RIGHTS = '''        Capability("Add and change allowance types", "payroll.add_allowancetype",
                   "Also switches an allowance on or off."),
        Capability("See loans and salary advances", "payroll.view_loan",
                   "Shows the Loans tab with every balance and schedule."),
        Capability("Prepare loans", "payroll.add_loan", "Create a loan, change a draft, cancel one before it is paid out."),
        Capability("Send a loan for approval", "payroll.submit_loan", "Management then decides it in the Approvals inbox."),
        Capability("Record loan payouts and repayments", "payroll.pay_loan",
                   "The bank transfer, early payments, pre-closure, skipping a month, rescheduling, waiving a balance."),
        Capability("Add a loan that was agreed verbally", "payroll.agree_loan",
                   "Skips the approval: HR records who agreed it and when."),
    ],'''

TAB_OLD = ("  {% if perms.payroll.view_finalsettlement %}<li class=\"nav-item\"><a class=\"nav-link{% if tab == 'settlements' %} "
           "active{% endif %}\" href=\"{% url 'web:payroll_settlements' %}\">Final settlements</a></li>{% endif %}")
TAB_NEW = ("  {% if perms.payroll.view_loan %}<li class=\"nav-item\"><a class=\"nav-link{% if tab == 'loans' %} active{% endif %}\" "
           "href=\"{% url 'web:payroll_loans' %}\">Loans</a></li>{% endif %}\n" + TAB_OLD)

ROUTES = '''    path("payroll/loans/", payroll_loans.loan_list, name="payroll_loans"),
    path("payroll/loans/new/", payroll_loans.loan_new, name="payroll_loan_new"),
    path("payroll/loans/pick/", payroll_loans.loan_pick, name="payroll_loan_pick"),
    path("payroll/loans/<int:pk>/", payroll_loans.loan_detail, name="payroll_loan"),
    path("payroll/loans/<int:pk>/edit/", payroll_loans.loan_edit, name="payroll_loan_edit"),
    path("payroll/loans/<int:pk>/statement/", payroll_loans.loan_statement, name="payroll_loan_statement"),
    path("payroll/loans/<int:pk>/submit/", payroll_loans.loan_submit, name="payroll_loan_submit"),
    path("payroll/loans/<int:pk>/withdraw/", payroll_loans.loan_withdraw, name="payroll_loan_withdraw"),
    path("payroll/loans/<int:pk>/cancel/", payroll_loans.loan_cancel, name="payroll_loan_cancel"),
    path("payroll/loans/<int:pk>/payout/", payroll_loans.loan_payout, name="payroll_loan_payout"),
    path("payroll/loans/<int:pk>/payment/", payroll_loans.loan_payment, name="payroll_loan_payment"),
    path("payroll/loans/<int:pk>/preclose/", payroll_loans.loan_preclose, name="payroll_loan_preclose"),
    path("payroll/loans/<int:pk>/skip/", payroll_loans.loan_skip, name="payroll_loan_skip"),
    path("payroll/loans/<int:pk>/reschedule/", payroll_loans.loan_reschedule, name="payroll_loan_reschedule"),
    path("payroll/loans/<int:pk>/waive/", payroll_loans.loan_waive, name="payroll_loan_waive"),
'''
LAST_ROUTE = '    path("payroll/runs/<int:pk>/lines/<int:line_pk>/payslip/", payroll_runs.payslip, name="payroll_payslip"),\n]'


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    root = Path(fix_path(args[0])) if args else Path.cwd()
    pay = root / "apps" / "payroll"
    if not (root / "manage.py").exists() or not pay.is_dir():
        sys.exit(f"{root} does not look like the Ivhrms project (manage.py and apps/payroll are expected).")
    for needed in ("finalsettlement.py", "settlements.py", "runs.py", "signals.py", "settings_spec.py"):
        if not (pay / needed).exists():
            sys.exit(f"apps/payroll/{needed} is missing. Payroll runs and final settlements must be installed first.")
    print(f"Project: {root}\n\n1. Files")
    copy_files(root)

    print("\n2. Changes to existing files")
    append(root, "apps/payroll/models.py", "payroll/models.py: load the loan tables",
           "from . import loans  # noqa: E402,F401  (loans and salary advances)", "from . import loans")
    edit(root, "apps/payroll/payrun.py", "payrun.py: a payroll deduction can be a loan instalment",
         '        FINE = "fine", "Vehicle fine"\n        OTHER = "other", "Other"\n',
         '        FINE = "fine", "Vehicle fine"\n        LOAN = "loan", "Loan instalment"\n        OTHER = "other", "Other"\n',
         'LOAN = "loan", "Loan instalment"')
    edit(root, "apps/payroll/finalsettlement.py", "finalsettlement.py: a settlement line can be a loan balance",
         '        GRATUITY = "gratuity", "Gratuity"\n        ADDITION = "addition", "Addition"\n',
         '        GRATUITY = "gratuity", "Gratuity"\n        LOAN = "loan", "Loan balance"\n        ADDITION = "addition", "Addition"\n',
         'LOAN = "loan", "Loan balance"')
    edit(root, "apps/payroll/runs.py", "runs.py: pick up loan instalments with the other charges",
         '                          f"Fine {f.reference or f.pk}, {f.offence} ({f.vehicle.plate_number})", f.display_amount))\n'
         '    return found\n\n\n@transaction.atomic\ndef import_charges(run, user):',
         '                          f"Fine {f.reference or f.pk}, {f.offence} ({f.vehicle.plate_number})", f.display_amount))\n'
         '    from .loan_services import loan_candidates\n\n    found += loan_candidates(run, taken, ids)\n'
         '    return found\n\n\n@transaction.atomic\ndef import_charges(run, user):',
         "found += loan_candidates(run, taken, ids)")
    edit(root, "apps/payroll/runs.py", "runs.py: an approved run counts its instalments as repaid",
         '            problems.append(f"Fine {fine.reference or fine.pk}: {exc}")\n'
         '    return ("Approved, but check: " + "; ".join(problems))[:500] if problems else ""\n\n\n'
         '@transaction.atomic\ndef cancel_run(run):',
         '            problems.append(f"Fine {fine.reference or fine.pk}: {exc}")\n'
         '    from .loan_services import mark_instalments_deducted\n\n    problems += mark_instalments_deducted(run)\n'
         '    return ("Approved, but check: " + "; ".join(problems))[:500] if problems else ""\n\n\n'
         '@transaction.atomic\ndef cancel_run(run):',
         "problems += mark_instalments_deducted(run)")
    edit(root, "apps/payroll/settlements.py", "settlements.py: the loan balance on a settlement",
         "        SettlementItem.objects.create(settlement=case, kind=kind, label=label, amount=money(amount), is_system=True, position=n)\n"
         "    total = sum((i.amount for i in case.items.all()), ZERO)\n",
         "        SettlementItem.objects.create(settlement=case, kind=kind, label=label, amount=money(amount), is_system=True, position=n)\n"
         "    from .loan_services import settlement_loan_lines\n\n    issues.extend(settlement_loan_lines(case))\n"
         "    total = sum((i.amount for i in case.items.all()), ZERO)\n",
         "settlement_loan_lines(case)")
    edit(root, "apps/payroll/settlements.py", "settlements.py: an approved settlement counts the balance as repaid",
         '            problems.append(f"Fine {fine.reference or fine.pk}: {exc}")\n'
         '    return ("Approved, but check: " + "; ".join(problems))[:500] if problems else ""\n\n\n'
         '@transaction.atomic\ndef cancel_settlement(case):',
         '            problems.append(f"Fine {fine.reference or fine.pk}: {exc}")\n'
         '    from .loan_services import recover_settlement_loans\n\n    problems += recover_settlement_loans(case)\n'
         '    return ("Approved, but check: " + "; ".join(problems))[:500] if problems else ""\n\n\n'
         '@transaction.atomic\ndef cancel_settlement(case):',
         "problems += recover_settlement_loans(case)")
    append(root, "apps/payroll/signals.py", "signals.py: a decided loan", SIGNAL, "def loan_decided")
    edit(root, "apps/payroll/settings_spec.py", "settings_spec.py: the Loans settings function",
         'register(Module(\n    key="payroll", label="Payroll", order=55, icon="bi-cash-coin",',
         SETTINGS_FUNCTION + 'register(Module(\n    key="payroll", label="Payroll", order=55, icon="bi-cash-coin",',
         "def _loan_settings")
    edit(root, "apps/payroll/settings_spec.py", "settings_spec.py: the Loans section",
         '            template="web/settings/_payroll_allowances.html", context=allowance_context),\n    ],\n    capabilities=[',
         SETTINGS_SECTION, 'title="Loans"')
    edit(root, "apps/payroll/settings_spec.py", "settings_spec.py: the loan rights",
         '        Capability("Add and change allowance types", "payroll.add_allowancetype",\n'
         '                   "Also switches an allowance on or off."),\n    ],',
         SETTINGS_RIGHTS, 'payroll.view_loan')
    edit(root, "apps/web/forms/payroll_runs.py", "forms/payroll_runs.py: a loan is not a manual reason",
         "                     if c not in (PayrollAdjustment.Category.RENEWAL, PayrollAdjustment.Category.FINE)]",
         "                     if c not in (PayrollAdjustment.Category.RENEWAL, PayrollAdjustment.Category.FINE,\n"
         "                                  PayrollAdjustment.Category.LOAN)]",
         "PayrollAdjustment.Category.LOAN")
    edit(root, "apps/web/templates/web/payroll/_tabs.html", "payroll/_tabs.html: the Loans tab", TAB_OLD, TAB_NEW,
         "web:payroll_loans")
    edit(root, "apps/web/urls/payroll.py", "urls/payroll.py: import",
         "from apps.web.views import payroll, payroll_runs, payroll_settlements\n",
         "from apps.web.views import payroll, payroll_loans, payroll_runs, payroll_settlements\n", "payroll_loans, payroll_runs")
    edit(root, "apps/web/urls/payroll.py", "urls/payroll.py: the routes", LAST_ROUTE, ROUTES + LAST_ROUTE,
         "payroll_loans.loan_list")
    edit(root, "apps/web/presenters.py", "presenters.py: import",
         "from apps.airtickets.ticketrequests import TicketRequest\n",
         "from apps.airtickets.ticketrequests import TicketRequest\nfrom apps.payroll.loans import Loan\n",
         "from apps.payroll.loans import Loan")
    edit(root, "apps/web/presenters.py", "presenters.py: show a loan in the Approvals inbox",
         "    if isinstance(t, TicketRequest):\n",
         '    if isinstance(t, Loan):\n        return {"kind": "Loan", "title": t.title, "detail": t.detail, "reason": t.reason}\n'
         "    if isinstance(t, TicketRequest):\n", "isinstance(t, Loan)")

    print("\n3. Migration")
    if "--no-migrations" in sys.argv:
        print("  skipped (--no-migrations). Run: python manage.py makemigrations payroll -n loans")
    else:
        run = subprocess.run([sys.executable, "manage.py", "makemigrations", "payroll", "-n", "loans"],
                             cwd=root, capture_output=True, text=True)
        print("  " + (run.stdout.strip() or run.stderr.strip()).replace("\n", "\n  "))
        if run.returncode != 0:
            manual_items.append("makemigrations")
            print("  MANUAL  makemigrations did not finish. Activate the virtualenv (ivenv) and run:\n"
                  "          python manage.py makemigrations payroll -n loans")
    if manual_items:
        print(f"\n{len(manual_items)} item(s) need you. Do the MANUAL steps above, then continue with NOTES.md.")
        sys.exit(1)
    print("\nDone. Next: python manage.py migrate   then   python manage.py test apps.payroll apps.web   (see NOTES.md)")


if __name__ == "__main__":
    main()
