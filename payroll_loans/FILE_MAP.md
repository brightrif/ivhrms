# Loans (Payroll > Loans): where each file belongs

Project folder: `C:\Django\Projects\Ivhrms` (Git Bash: `/c/Django/Projects/Ivhrms`).
One migration (new tables, and two lists of choices that gain a "loan" option). `install.py` copies the files, makes the
small edits and runs `makemigrations`. This table is only so you can see what goes where, or do a MANUAL step.

## New files

| File | What it is |
|---|---|
| `apps/payroll/loans.py` | the tables: Loan, LoanInstalment (the schedule), LoanPayment (what was repaid); the rights for HR and Management; the approval flow "Loan approval" |
| `apps/payroll/loan_services.py` | the rules: create, verbal agreement, limits, approval, payout, payroll pick-up, early payment, skip, reschedule, waive, final settlement |
| `apps/payroll/loan_settings.py` | the Loans section of Payroll in Settings: default interest and three optional limits |
| `apps/payroll/test_loans.py` | tests |
| `apps/web/forms/payroll_loans.py` | the forms |
| `apps/web/views/payroll_loans.py` | the pages |
| `apps/web/templates/web/payroll/loans.html`, `loan_pick.html`, `_loan_pick_results.html`, `loan_form.html`, `loan.html`, `loan_confirm.html`, `loan_statement.html` | the list, the person picker, the new-loan form, one loan, the cancel question, the printable statement |
| `apps/web/tests/test_payroll_loans.py` | tests |

## Small edits made by the installer

| File | The change |
|---|---|
| `apps/payroll/models.py` | one line at the end loads the loan tables |
| `apps/payroll/payrun.py` | a payroll deduction can be a "Loan instalment" |
| `apps/payroll/finalsettlement.py` | a settlement line can be a "Loan balance" |
| `apps/payroll/runs.py` | "Pick up charges" also picks up loan instalments; approving a run counts them as repaid |
| `apps/payroll/settlements.py` | a settlement shows the loan balance as a line; approving it counts the balance as repaid |
| `apps/payroll/signals.py` | a decided loan approval makes the schedule |
| `apps/payroll/settings_spec.py` | the Loans section of Payroll in Settings, and five rights on the Access page |
| `apps/web/forms/payroll_runs.py` | a loan is not offered as a manual adjustment reason |
| `apps/web/templates/web/payroll/_tabs.html` | the Loans tab, between Payroll runs and Final settlements |
| `apps/web/urls/payroll.py` | the Loans routes |
| `apps/web/presenters.py` | a loan reads properly in the Approvals inbox |

## Created by `makemigrations`

| File |
|---|
| `apps/payroll/migrations/NNNN_loans.py` |
