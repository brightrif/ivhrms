# Payslips (print or save as PDF)

Small change set: 2 new files and 9 small edits to files you already have. No migration, no new package.

## What changes

| File | Change |
|---|---|
| `apps/web/templates/web/payroll/payslip.html` | **New.** A clean page, one payslip per page, with a "Print / Save as PDF" button. No sidebar, so it prints cleanly |
| `apps/web/tests/test_payslips.py` | **New.** 15 tests |
| `apps/payroll/calculation.py` | The salary's make-up (basic and each allowance) is now kept on each line, so the slip can list them. 3 small edits |
| `apps/payroll/payrun.py` | 3 read-only helpers on a line: `iban_masked`, `total_earnings`, `total_deductions`. No database change |
| `apps/web/views/payroll_runs.py` | Two views: all payslips of a run, and one person's payslip. Only for an approved or paid run. Every view is written to the audit log, per person |
| `apps/web/urls/payroll.py` | Two URLs: `payroll/runs/<id>/payslips/` and `payroll/runs/<id>/lines/<id>/payslip/` |
| `apps/web/templates/web/payroll/run.html` | The "Excel" button becomes "Downloads" and gains "All payslips (print or save as PDF)" once the run is approved |
| `apps/web/templates/web/payroll/line.html` | A "Payslip" button on a person's line once the run is approved |

Same right as the rest of the run: **See payroll runs and their payment lists**. Nobody new gets access.

## What is on a payslip

- Company, month and period, employee name and number, how paid (the IBAN is masked: `BH67 **** 3456`), and the paid date once paid.
- **Staff:** basic salary and each allowance. For a joiner or a mid-month raise it shows the salary day by day instead.
- **Labor:** wages by kind (daily, hourly, monthly, holiday and leave pay), approved overtime and premium with its hours, days worked and hours.
- Additions, then deductions (absence and unpaid days, the employee's SIO share, each deduction you added), and net pay.
- **Not on the slip:** the company's SIO share (it is a cost, not the employee's money).

A line calculated before this change has no salary make-up stored. Its slip shows the salary as a day-by-day line and
still prints correctly. Press **Recalculate** on a draft run to get the full make-up.

## Steps (Git Bash)

```bash
cd /c/Django/Projects/Ivhrms
git status                      # commit anything pending first
python /c/Django/payslips/apply_payslips.py
```

Expected report: `Edited: 9   New files: 2`. Run it twice if you like: the second time says `Already in place: 11`.
If an edit cannot find its place, it changes nothing there and prints the exact text to add by hand.

```bash
python manage.py test apps.payroll apps.web.tests.test_payroll apps.web.tests.test_payroll_runs apps.web.tests.test_payroll_labor apps.web.tests.test_payslips
```

Expected: `Ran 210 tests ... OK`.

## Try it

Open an **approved** run, then **Downloads > All payslips**, or open one person's line and press **Payslip**. In the print
window choose **Save as PDF**. Try the same on a draft run: it is refused with a message.

## Why print and not a PDF file

Your project has no PDF library, and your reports already use the browser's print. Printing also draws names in any
language correctly. If you later want a file the system can email or store, I would add a PDF library (it needs a font
and extra work for Arabic text) and an "email payslip" step.
