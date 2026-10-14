# Payroll: the direct labor run

## What you get

| Where | What |
|---|---|
| **Payroll > Payroll runs > New run** | A new choice, **Who is paid**: Staff or Direct labor |
| A labor run's page | Every direct labor worker with their pay, what is blocking each line, additions and deductions, Excel downloads |
| A labor line's page | What the labor records say: pay basis, days worked, hours, wages, approved overtime |
| The labor module | Seven small lock checks (see "What it does to the labor module") |

A labor run uses the same flow as the staff run: Draft, Management approves in the Approvals inbox, frozen, payments
recorded by bank or cash, Excel register, bank transfer sheet and cash list. The rights are the same payroll rights as
for the staff run; nothing new to give out.

This package contains the whole payroll module, so it works on a project that has phases 1 and 2 and on one that has
nothing yet.

---

## How a labor worker is paid (please read: these are my decisions)

The pay follows the rules your labor module already uses (the ones the cost report documents), applied to the dated
pay terms in force on each day:

- **Daily wage:** the daily rate for each day worked (a half day is half).
- **Hourly:** the hourly rate for the hours on the confirmed timesheet.
- **Monthly salary:** see the next section.
- **Friday and holiday premium:** when the worker's terms have it on, those hours are paid through the overtime claim at
  the company's premium multiplier, so the day is **not** also paid as a normal day.
- **Paid holidays and paid leave:** when the terms say so, a public holiday or a paid-leave day not worked is paid the
  standard day (the daily rate, or the standard hours at the hourly rate). A monthly salary already covers them.
- **Overtime:** only the claims Management has **approved**. The claims are read as they are; they are never priced again.
- **SIO** (Bahraini workers only) is taken on the wages earned, **leaving the overtime out**, kept between the lowest and
  highest wage in the payroll policy. Please confirm with the accountant that overtime is not part of the SIO wage.
- Additions, deductions, charges (renewal fees and vehicle fines) and payments work exactly as in the staff run.

### One place where payroll deliberately differs from the cost report

The cost report pays a monthly salary for **every calendar day**, at salary divided by the "days in a month" of the
overtime rules (30). In a 31-day month that adds up to 31/30 of the salary: a 300 BHD salary shows as 310. That is
acceptable as a cost estimate and wrong as pay. In payroll, a monthly worker earns the salary divided by the days in the
month for each day employed (so a full month is exactly the salary, a joiner mid-month earns the days employed), and an
absent or unpaid day costs the salary divided by the overtime rules' "days in a month", the same rule staff follow. I did
not change the cost report. You may want to look at it, because it overstates monthly-salary cost in 31-day months.

### A line is blocked, never guessed

You chose that unsettled numbers must stop a line. A line is blocked when:

- hours are still **draft**, or a worked day has **no hours** (for a worker whose pay depends on hours: hourly, or
  overtime or premium on);
- hours exist for a day that attendance does not show as worked;
- confirmed overtime or premium hours have **no claim yet**, or a claim is **still pending approval**;
- a **daily or hourly** worker has working days with **no attendance entry** (they would silently go unpaid; mark each day
  present or absent on the site sheet). A **monthly** worker only gets a warning, because the salary pays those days;
- pay terms are missing for the days worked;
- the nationality is blank or not recognised, or a Bahraini has no SIO rates in the policy;
- no payment details, or a negative net.

A worker with nothing at all this month gets an amber warning, not a block.

### Who is in a labor run

Direct labor (engagement "direct") who had joined by the end of the month and are not Separated. Contractor-supplied
labor is paid through the contractor statement and is never in payroll. A labor worker with **no labor profile** has no
pay terms, so they cannot be paid; the run page names them in a yellow notice so nobody is forgotten.

A labor run can only be made **once the month is over**, because hours, attendance and overtime are only complete then.

---

## What it does to the labor module

Once a labor run is **approved**, that month is closed for the workers in it. The labor module now refuses, with a clear
message, to:

1. start **new pay terms** on or before the end of the approved month (after it is fine),
2. **save hours**, 3. **reopen** a confirmed period, or 4. **divide hours** between projects, for that month,
5. **prepare**, 6. **decide**, or 7. **void** overtime claims for that month.

The message reads, for example: "Payroll for March 2026 (IV1) has already been approved, so changing hours is not
allowed. Add an adjustment to the next payroll run for any difference."

How it is done, so you can judge the risk: a new file, `apps/labor/locks.py`, holds a tiny registry, and seven places in
four labor files (`services.py`, `timekeeping.py`, `hours_split.py`, `overtime_services.py`) each get **two added lines**
(14 lines in all). Nothing existing is rewritten. Payroll registers the rule that closes an approved labor month. The
installer finds each place by its exact surrounding text and **refuses to guess**: if a place is not found, it prints the
exact lines to add by hand and changes nothing there.

**Not locked:** attendance and site allocation. They also feed staff payroll, so locking them is a separate decision.
Changing attendance for an approved month does not alter the frozen run, but the labor records would then disagree with
what was paid.

---

## Step 1: open Git Bash and get ready

```bash
cd /c/Django/Projects/Ivhrms
```

Activate `ivenv` the way you normally do, then:

```bash
git status
```

Commit anything pending, then start a branch:

```bash
git checkout -b payroll-labor
```

## Step 2: put the package outside the project

Unzip `payroll_labor.zip`, for example to `C:\Django\payroll_labor`. Inside you should see:

```
payroll_labor/
    install_payroll_labor.py
    README_payroll_labor.md      (this file)
    files/                       (the whole payroll module, plus apps/labor/locks.py)
```

## Step 3: run the installer

```bash
cd /c/Django/Projects/Ivhrms
python /c/Django/payroll_labor/install_payroll_labor.py
```

Read the report:

- **New files copied**: a few (`labor_payroll.py`, `locks.py` in payroll, `apps/labor/locks.py`, the labor tests, the 0003 migration, ...).
- **Earlier files updated**: files still exactly as I gave them before; they are updated.
- **Files you have changed, so yours was kept**: only if you edited a payroll file. The new version is saved beside it with
  a `.payroll3` ending. Compare and merge, or send me both.
- **Existing files edited: 7**, the labor lock checks (plus the sidebar link if it is not already current).
- **DO THESE BY HAND**: only if a place was not found. It prints the exact lines. Send me the report if you see this.

To see exactly what changed in your labor files:

```bash
git diff apps/labor
```

You should see only added lines containing `locks.ensure_open` and `from . import locks  # payroll lock: ...`.

## Step 4: check the migrations, then migrate

```bash
python manage.py makemigrations --check --dry-run
python manage.py migrate
```

Expected: `No changes detected`, then `Applying payroll.0003_labor_runs... OK`. Nothing in your data changes.

## Step 5: run the tests

Payroll, which now also exercises your real labor module:

```bash
python manage.py test apps.payroll apps.web.tests.test_payroll apps.web.tests.test_payroll_runs apps.web.tests.test_payroll_labor
```

Expected: `Ran 195 tests ... OK`. **Then your own labor tests, because this package edited labor files:**

```bash
python manage.py test apps.labor
```

Those must pass exactly as before. Then the whole suite (`python manage.py test`). If anything fails, send me the failure text.

## Step 6: try a labor run by hand

1. Pick a finished month. Check that the site attendance sheet and the timesheet are complete and **confirmed**, and that
   overtime claims are **prepared and approved** (Overtime).
2. **Payroll > Payroll runs > New run**, choose **Direct labor** and the month.
3. Read the red and amber lines. Each says what to fix: hours draft or missing, overtime to claim or approve, attendance
   to fill in, pay terms, nationality, payment details.
4. Fix them in Labor, then press **Recalculate** until nothing is red. Open a person to see the workings.
5. Add a deduction (an advance, for example), then **Send for approval**. Approve it as a Management user in **Approvals**.
6. **Record bank transfers** and **Record cash**, and download the Excel files.
7. Now try to **change that worker's pay terms from a date in that month** (Labor, change pay) and to **reopen the hours**:
   both should be refused with the message above. Starting new pay terms on the first day of the next month should work.

## Step 7: commit

```bash
git status
git add -A
git commit -m "Payroll: direct labor run, with locks on approved labor months"
```

`git status` should show the payroll files, `apps/labor/locks.py` and the four edited labor files. If you see
`.payroll3` files, deal with them first.

---

## If something goes wrong

| What you see | What to do |
|---|---|
| A `.payroll3` file appears | You changed that file. Compare, merge, or send me both |
| "DO THESE BY HAND" for a labor file | The text around the place was not found. Send me the report; the lines are printed for you |
| "is not over yet" | A labor run needs the month to have ended |
| A line says "no pay terms" | The worker has no pay terms for some day worked. Set them under Labor |
| "has no labor profile" notice | Add a labor profile for that worker under Labor |
| A labor test fails after the install | Send me the failure text. To remove only the lock checks, restore the four labor files with `git checkout -- apps/labor` and delete `apps/labor/locks.py` |

To undo everything before merging: `python manage.py migrate payroll 0002`, then `git checkout main`, then
`git branch -D payroll-labor`.

---

## Questions for the accountant and the client

- Is **overtime part of the SIO wage** for Bahraini labor? (I left it out.)
- Should a daily or hourly worker with **no attendance entry** on a working day really stop the line? (I think so: they
  would otherwise silently go unpaid. It can be softened to a warning.)
- For monthly-salary workers, is **salary divided by the days in the month** the right way to pay a joiner or a leaver?
  (It is what staff get.)

## Two things I noticed in your labor module (I did not change them)

- **The hourly rate is labelled "per month".** In `LaborRate.unit` (`apps/labor/models.py`), anything that is not a daily
  wage returns "month", so an hourly worker shows as, for example, "1.500 BHD / month". It should say "hour". It is only the
  label; the pay maths is right.
- **The cost report overstates a monthly salary in a 31-day month** (31/30 of it), explained near the top of these notes.
  Payroll does not have this problem.

## Not in this package

- **Payslip PDFs**, for staff and labor.
- **Locking attendance and allocation** for an approved month.
- **Final settlement, loans and food allowance**, and a bank file for the Wage Protection System.
