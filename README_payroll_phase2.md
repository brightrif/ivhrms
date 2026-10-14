# Payroll phase 2: payroll policy, nationality check, staff payroll runs

## If you already installed the first phase 2 package: update 1

This package replaces it. Run the installer exactly as in Step 3 below; it updates the files it gave you before
(it reports them as "Earlier files updated") and keeps anything you changed. No new migration, nothing to redo. What changed:

- **Tests only, for your six errors and one failure.** In your project a compliance document needs an expiry date and a
  document type, which my test set-up had left out. The tests now build renewal payments through the compliance module's own
  service. The "Hana" failure was the logged-in user's name showing in the top bar; the test now checks who is offered.
- **A real fix: the Payroll sidebar link.** Someone with only run rights (Finance or Management, for example) had no link
  and the People tab would have ended on a "no permission" page. They now get a Payroll link that opens the runs, and the
  People tab shows only to those who may see salaries. The installer replaces the link in `base.html` for you.

After the installer: `python manage.py test apps.payroll apps.web.tests.test_payroll apps.web.tests.test_payroll_runs`
should say `Ran 145 tests ... OK`.

---

## What you get

| Where | What |
|---|---|
| **Payroll > Policy** (and Settings > Payroll) | The payroll policy for each company, dated: days in a month and the SIO rules |
| **Payroll > People** | A new button: staff whose nationality payroll cannot read (SIO depends on it) |
| **Payroll > Payroll runs** | One staff run per company per month: calculate, adjust, send for approval, record payments |
| A run's page | Totals, a line for every person with their problems, additions and deductions, Excel downloads |
| A line's page | The workings: salary day by day, the days that cost pay, SIO, adjustments, net |
| **Approvals** (existing inbox) | Management approves a payroll run there, like any other request |
| Excel downloads | Payroll register, bank transfer sheet, cash pay list (with a signature column) |

Everything from phase 1 stays. This package contains the whole payroll module, so it works on a project that has
phase 1 and on one that has nothing yet.

---

## How the pay is worked out (please read: these are my decisions)

1. **A day employed earns that day's salary divided by the days in that calendar month.** A full month is exactly
   the monthly salary, whether the month has 28, 30 or 31 days. A joiner part-way through the month, or a raise part-way
   through, is paid day by day at the salary in force on each day.
2. **A day lost costs the salary of that day divided by "days in a month" (30).** This is the rule you chose. It uses
   the paid fractions the attendance module already defines: absent and unpaid leave lose the whole day, a half day
   loses half, paid leave, holidays, weekly offs and late or early lose nothing.
3. **A working day with no attendance entry is paid in full**, and the person's line turns amber so HR can check it.
   (Days that have not happened yet, and weekly offs and holidays, are not counted as missing.)
4. **SIO is taken only from Bahraini staff.** The wage is basic salary plus the allowances you ticked as "counts towards
   the SIO wage", kept between the lowest and highest wage in the policy. The employee share comes off the pay. The
   company's share is shown as a cost and is not on the payslip. Unpaid days reduce the SIO wage only if the policy says so.
5. **Nothing about SIO is guessed.** A Bahraini with no SIO rates in the policy, or anyone whose nationality is blank or
   not recognised (for example "Bahrani"), stops the run until it is fixed.
6. **Net pay** = gross, less absence, less the employee's SIO, plus additions, less deductions. A negative net blocks the run.
7. **Who is in a run:** staff of the company who had joined by the end of the month. People on notice are included.
   People marked **Separated are not**: they are paid through final settlement, which is not built yet. Direct labor will
   have its own run later. Contractor-supplied labor is never in payroll.

Please confirm points 1, 3 and 7 with the client, and point 4 with the accountant.

## The flow of a run

**Draft** (HR calculates and adjusts, as often as needed) > **Waiting for approval** (Management, in the Approvals inbox) >
**Approved** (frozen) > **Paid** (when every person has been marked paid).

- A rejected run, or one you take back, returns to Draft with the reason shown.
- While a run waits, or once it is approved, nothing can recalculate or adjust it.
- **Approved pay is locked.** A new salary cannot start on or before the end of an approved month, an approved month's
  salary cannot be corrected, and a new policy cannot start inside it. Start the change after the month and add an
  adjustment to the next run for any difference.
- **Pick up charges** adds, as deductions, the renewal fees and vehicle fines already marked "charge to the employee".
  Each can be removed again and each is only ever deducted once. When the run is approved, the fines count as recovered
  from the driver in Vehicles.
- The person who sends a run cannot approve it (the approval engine already forbids it). The approver is anyone in the
  **Management** group, through a flow called "Payroll approval" that you can edit with the other approval flows.

---

## Step 1: open Git Bash and get ready

```bash
cd /c/Django/Projects/Ivhrms
```

Activate `ivenv` the way you normally do. Then:

```bash
git status
```

If it lists changes, commit them first. Then start a branch:

```bash
git checkout -b payroll-phase2
```

## Step 2: put the package outside the project

Unzip `payroll_phase2.zip`, for example to `C:\Django\payroll_phase2`. Inside you should see:

```
payroll_phase2/
    install_payroll_phase2.py
    README_payroll_phase2.md      (this file)
    files/                        (the whole payroll module)
```

## Step 3: run the installer

```bash
cd /c/Django/Projects/Ivhrms
python /c/Django/payroll_phase2/install_payroll_phase2.py
```

Read the report:

- **New files copied**: about 20 on top of phase 1 (none, if you already have the first phase 2 package).
- **Earlier files updated**: files that are still exactly as I gave them before (phase 1, or the first phase 2 package), so they are updated.
- **Files you have changed, so yours was kept**: only appears if you edited one of the payroll files. Yours is untouched
  and the new version is saved next to it with a `.payroll2` ending. Compare the two (`git diff --no-index a b`) and merge,
  or send me both.
- **Existing files edited**: normally 1, the Payroll link in the sidebar, which now also serves people with only run rights.
- **DO THESE BY HAND**: only if a place to edit was not found. It prints the exact lines.

It is safe to run twice.

## Step 4: check Django agrees with the migrations

```bash
python manage.py makemigrations --check --dry-run
```

Expected: `No changes detected`. If it wants to create a payroll migration, send me the output.

## Step 5: create the new tables

```bash
python manage.py migrate
```

Expected line: `Applying payroll.0002_payroll_runs... OK`. This also creates the "Payroll approval" flow for Management.
Nothing in your existing data changes.

## Step 6: run the tests

```bash
python manage.py test apps.payroll apps.web.tests.test_payroll apps.web.tests.test_payroll_runs
```

Expected: `Ran 145 tests ... OK`. Then the whole suite:

```bash
python manage.py test
```

If anything fails, send me the failure text.

## Step 7: give the new rights

Sign in as the superuser, then **Settings > Access**. Six new rows appear under Payroll:

| Right | What it allows |
|---|---|
| See payroll runs and their payment lists | Opens the runs and shows every figure, including net pay |
| Prepare payroll runs | Start a run, recalculate, add or remove adjustments, pick up charges, cancel a draft |
| Send a payroll run for approval | The "Send for approval" and "Take back" buttons |
| Record payroll payments | Marks people as paid, by bank or cash |
| See the payroll policy | The policy history |
| Set the payroll policy | Days in a month and the SIO rules |

Nobody has them until you choose. A sensible split: HR gets "see", "prepare" and "send"; Finance gets "see" and "record
payments"; Management gets "see" (they approve in the Approvals inbox).

**Each person who will send a run needs their login linked to an employee record**, because the approval is sent in
their name.

## Step 8: set things up, in this order

1. **Settings > Payroll > Payroll policy > Change policy.** Set the policy for each company: start date (for example the
   first day of the year), days in a month (30), and the SIO rates. If the accountant has not given the rates yet, leave
   them empty. The run will then stop at each Bahraini until you fill them in with a new policy.
2. **Payroll > People.** Close the gaps the yellow buttons show: staff without a salary, people without payment details,
   and staff whose nationality is not recognised (fix the nationality on the staff record: write "Bahraini" for Bahrainis,
   and the country's usual name for everyone else, for example "Indian").
3. **Settings > Payroll > Allowance types.** Tick "counts towards the SIO wage" on the allowances the accountant says count.

## Step 9: try a run by hand

1. **Payroll > Payroll runs > New run**, choose a finished month.
2. Look at the red and amber lines. Open a person: the page shows the salary day by day and every day that cost pay.
3. Press **Add adjustment** and add a bonus; press **Pick up charges** if any fines or renewal fees were charged.
4. Press **Recalculate** after fixing an attendance entry or a salary, and see the line change. Your adjustments stay.
5. Press **Send for approval**. Sign in as a Management user, open **Approvals**, and approve.
6. Back on the run: **Record bank transfers** and **Record cash**, tick the people paid. When everyone is paid the run
   becomes Paid.
7. Download the **Excel** files: the register, the bank sheet and the cash list.
8. Try to change last month's salary after approval: it should be refused with an explanation.

## Step 10: commit

```bash
git status
git add -A
git commit -m "Payroll phase 2: policy, nationality check, staff payroll runs"
```

`git status` should show only the payroll files. If you see `.payroll2` files, deal with them first.

---

## If something goes wrong

| What you see | What to do |
|---|---|
| `STOP: Run this from the Ivhrms project folder` | `cd /c/Django/Projects/Ivhrms` and run it again |
| A `.payroll2` file appears | You changed that file. Compare, merge, or send me both |
| "has no payroll policy" when starting a run | Set the policy first (Step 8) |
| "problem to fix first" when sending | Open the red lines on the run: each says what to fix |
| "not linked to an employee record" | The sender's login needs an employee record |
| Nobody can approve | Management users need access to the company, and the group must be called Management |
| A test fails | Send me the failure text |

To undo everything before merging: `python manage.py migrate payroll 0001`, then `git checkout main`, then
`git branch -D payroll-phase2`.

---

## Questions for the accountant and the client

- The current **SIO rates**: employee and employer, for Bahrainis; and whether there is an employer rate for others.
- The lowest and highest **wage SIO counts**, if any.
- Which **allowances** count towards the SIO wage, and whether unpaid days reduce it.
- How **leavers** should be handled until final settlement is built. For now, keep someone as "On notice" until their
  last salary has been through a run.
- Whether a working day with **no attendance entry** should really be paid in full (it is flagged, not blocked).

## Not in this phase

- **Payslip PDFs.** The line page holds everything a payslip needs; the PDF comes next.
- **Direct labor payroll**, from the month-end timesheets and the pay terms under Labor.
- **Final settlement, loans and food allowance.** Until they exist, use adjustments (a salary advance, for example).
- **A bank file for the Wage Protection System.** The bank transfer sheet is an Excel list for now.
- **Nationality as a pick-list on the staff form.** I did not change the staff form. The nationality is recognised from
  what is typed, and anything unclear stops the run, so a typing mistake can never silently skip SIO.
