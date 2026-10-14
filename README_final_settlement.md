# Final settlement (Bahrain private-sector rules), staff

13 new files and 40 small edits to files you already have. One migration (`0004_final_settlement`), nothing in your data changes.

**Please have the client's legal adviser confirm the points marked "confirm" before this is used for real payments.** The rules
come from the SIO's gratuity pages, the LMRA wages and indemnity booklet, and law-firm summaries of Law 36 of 2012, not from
the law text itself.

## What it does

| Where | What |
|---|---|
| **Payroll > Final settlements** | A case for each leaver: why they leave, notice, last working day, then a line-by-line statement |
| A settlement | Red problems to fix, amber warnings, how each figure was worked out, a clearance checklist |
| **Statement** (print) | The statement with signature lines. Marked DRAFT until approved |
| **Certificate** (print) | The end-of-service certificate, once approved |
| **Approvals** | Management approves in the usual inbox ("Final settlement approval" flow, created by `migrate`) |
| **Payroll runs** | Show, for each non-Bahraini, the monthly SIO gratuity contribution the company pays the SIO (a company cost), in the run, on the line page and in the Excel register |
| **Allowance types** | A new tick, "Social allowance (counts for gratuity)" |

## The rules built in

1. **Wages** for the month of the last working day, to that day, by the same rules as payroll (a day earns salary divided by the
   days of that month; absence costs salary divided by 30; SIO taken from Bahrainis).
2. **Gratuity is for non-Bahrainis only.** Bahrainis are covered by SIO pension, so nothing is paid by the employer.
   It is worked on the **last basic wage plus the social allowance, if any** (nothing else): 15 days' wage for each of the first
   three years of service, then a month's wage for each year after, pro rata for part years.
3. **The 1 March 2024 split.** The employer pays directly only the gratuity for service up to 29 February 2024. The SIO pays the
   part earned since, from the contributions the company makes (4.2% of wage in the first three years of service, 8.4% after).
   The three-year bands run over the whole service, so the earlier part fills the first band first. The settlement pays the
   employer part and shows the SIO part as information.
4. **Annual leave** earned and not taken is paid. Your system grants a whole year's entitlement up front, so the settlement pays
   what has been earned by the last working day (every earlier year's balance, plus the part of the final year earned so far),
   at monthly wage divided by 30. Leave taken in advance is warned about and never deducted automatically.
5. **Notice** is at least 30 days. If the employer ends the contract with short notice, pay in lieu is added automatically; for a
   resignation the shortfall costs nothing unless HR chooses to recover it.
6. **Decisions the system will not make for you:** a dismissal without notice (Article 107) and a GCC national must have gratuity
   explicitly decided, with a reason when it is "not payable". A nationality that cannot be read blocks the case.
7. **Nobody is paid twice.** A person with a settlement is left out of payroll runs from the month of their last working day on.
   A settlement is refused if payroll has already paid that month. A run made earlier cannot be sent with that person in it.
8. **On approval** the person is marked **Separated** (as you chose), and any vehicle fines deducted count as recovered.

**Confirm with the legal adviser:** (a) is there a minimum service before gratuity is due (I applied none; part years are pro rata);
(b) is gratuity owed after a dismissal under Article 107 (left to HR to decide each time); (c) is the pre-2024 part worked on the
last wage (I used it) or on the wage at February 2024; (d) how GCC nationals are treated; (e) the deadline for paying a leaver,
and any limit on deductions from the final payment.

## The changes to existing files (applied for you)

`models.py` (the social-allowance tick, the gratuity wage, the new models), `nationality.py` (GCC check), `calculation.py`
(a part month earns by the whole month's length; the gratuity contribution), `runs.py` (settled people left out; charges shared
with settlements; contribution total), `defaults.py` and `signals.py` (the second approval flow), `settings_spec.py` (four rights),
`services.py`, `forms/payroll.py`, `views/payroll.py` and `allowances.html` (the tick), `urls/payroll.py`, `_tabs.html`,
`run.html`, `line.html` and `outputs.py` (the menu tab and the contribution).

## Steps (Git Bash)

```bash
cd /c/Django/Projects/Ivhrms
git status                       # commit anything pending first
python /c/Django/final_settlement/apply_final_settlement.py
python manage.py makemigrations --check --dry-run      # expect: No changes detected
python manage.py migrate                               # expect: Applying payroll.0004_final_settlement... OK
python manage.py test apps.payroll apps.web.tests.test_payroll apps.web.tests.test_payroll_runs apps.web.tests.test_payroll_labor apps.web.tests.test_payslips apps.web.tests.test_payroll_settlements
```

Expected: `Edited: 40   New files: 13`, then `Ran 285 tests ... OK`. If an edit cannot find its place it changes nothing there and
prints the text to add by hand. The new tests create annual leave types and balances in your real leave module; if a test
fails there, send me the failure text.

## Rights (Settings > Access)

Four new rows: **See**, **Prepare**, **Send for approval** and **Record payments** for final settlements. Give "See final
settlements" together with "See payroll runs", which shows the Payroll menu. Nobody has them until you choose.

## Try it

1. **Settings > Payroll > Allowance types:** tick "Social allowance" on the allowance (if the company has one).
2. **Payroll > Final settlements > New settlement**, choose the person, why, the last working day and the notice date.
3. Read the red and amber messages. Pick up charges (renewal fees, fines) with the button. Add a line for a loan balance.
4. **Send for approval**, approve it as Management, then **Record payment**. Print the statement and the certificate.
5. Check the person is now Separated and does not appear in that month's payroll run.

## Not in this package

Direct labor settlements (next), a return-ticket entry, the SIO portal filing itself, and any rule for deducting more than the
law allows from a final payment.
