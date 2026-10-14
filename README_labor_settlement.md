# Final settlement for direct labor

3 new files and 18 small edits. No migration. Needs the final settlement package already applied.

## What changes

| For | What |
|---|---|
| **Final settlements** | Direct labor can now be settled, like staff. The employee list offers staff and direct labor; contractor-supplied labor is refused (it is paid through the contractor), and so is a labor worker with no labor profile |
| **Last month's wages** | Worked out by the labor payroll rules, over the month up to the last working day, so it agrees with a labor run. A part month still earns by the whole month's length. Overtime is the approved claims only |
| **Blocks** | The same as a labor run: draft or missing hours, overtime not claimed or pending, attendance missing for a daily or hourly worker, no pay terms. Any of them stops the case being sent |
| **The monthly wage for the other dues** | Gratuity, leave and notice need one monthly wage, from the pay terms in force on the last day. A monthly salary as it is; a **daily rate x 30**; an **hourly rate x standard hours x 30**. It is written on the page. Labor has no allowances, so the gratuity wage is the same figure |
| **Labor runs** | A worker with a settlement is left out of labor runs from the month of the last working day, and a labor run made earlier cannot be sent with them in it |
| **Gratuity contribution** | Labor runs now show, for each non-Bahraini, the monthly SIO gratuity contribution the company pays the SIO (4.2% then 8.4%), worked on the wages earned, as for staff |
| **Locks** | An approved or paid settlement closes that month for the worker in the labor module (hours and overtime), the same way an approved labor run does |

Everything else (the Bahrain gratuity rules, the 1 March 2024 SIO split, leave earned, notice, approval, marking the person
Separated, the statement and certificate) is as in the staff settlement.

Please confirm with the accountant, as for labor runs: SIO and the gratuity contribution are worked on the wages earned, with
overtime left out. And confirm that daily rate x 30 and hourly rate x standard hours x 30 is the monthly wage the client uses
for a daily or hourly worker's gratuity, leave and notice.

## The edits (applied for you)

`settlements.py` (labor allowed; its wages and monthly wage come from the new `settlement_labor.py`), `labor_payroll.py`
(part months, settled workers left out, the contribution), `locks.py` (a settlement closes the month), `settlement.html` (where
the monthly wage came from), `forms/payroll_settlements.py` (staff and direct labor in the list), and the one old test that said
labor was not covered yet, in `test_settlements.py`.

## Steps (Git Bash)

```bash
cd /c/Django/Projects/Ivhrms
git status                       # commit anything pending first
python /c/Django/labor_settlement/apply_labor_settlement.py
python manage.py test apps.payroll apps.web.tests.test_payroll apps.web.tests.test_payroll_runs apps.web.tests.test_payroll_labor apps.web.tests.test_payslips apps.web.tests.test_payroll_settlements apps.web.tests.test_payroll_settlement_labor
```

Expected: `Edited: 18   New files: 3`, then `Ran 301 tests ... OK`. Safe to run twice. If an edit cannot find its place it changes
nothing there and prints the text to add by hand. The new tests use your real labor module (workers, hours, overtime claims).
