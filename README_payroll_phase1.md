# Payroll phase 1: staff salary structure, allowance types, payment details

## What you get

| Where | What |
|---|---|
| **Payroll** in the sidebar | A list of everyone payroll pays (staff and direct labor), with the gaps to close: staff without a salary, people without payment details |
| One person's Payroll page | Current salary (basic plus allowances), full salary history, payment details, buttons to set, change or correct the salary |
| **Staff page** | A small Pay card under the documents card |
| **Settings > Payroll** | Allowance types for each company (housing, transport and so on) |
| **Settings > Access** | Five new rights to give to groups. **Nobody has them until you choose.** |

Rules it enforces:

- Salaries are **effective-dated**. A new salary ends the old one the day before, so a past month can always be priced as it was.
- A salary cannot start before the joining date, and a new one must start after the current one.
- **Correct latest** fixes a mistake in place. It needs a written reason and works only on the latest salary.
- Salary amounts and IBANs are **hidden in the audit log**: it records that they changed, not the numbers.
- An allowance cannot be switched off while anyone still receives it.
- Payroll covers **staff and direct labor**. Labor supplied by a contractor is left out; it stays on the contractor statement.
- Labor pay terms stay under Labor. The Payroll page only links to them and holds the payment details.
- Bank details need the bank name and a valid IBAN (the check digits are verified, so typing mistakes are caught).

This phase changes no existing behaviour and adds new tables only.

---

## Step 1: open Git Bash and get ready

```bash
cd /c/Django/Projects/Ivhrms
```

Activate `ivenv` the way you normally do. Then:

```bash
git status
```

If it lists changes, commit them first (`git add -A` then `git commit -m "..."`). Then start a branch for this work:

```bash
git checkout -b payroll-phase1
```

## Step 2: put the package somewhere outside the project

Unzip `payroll_phase1.zip`, for example to `C:\Django\payroll_phase1`. Inside it you should see:

```
payroll_phase1/
    install_payroll_phase1.py
    README_payroll_phase1.md      (this file)
    files/                        (the new code)
```

Keep it **outside** the project folder, so nothing extra ends up in Git.

## Step 3: run the installer

You must be in the project folder (the one with `manage.py`):

```bash
cd /c/Django/Projects/Ivhrms
python /c/Django/payroll_phase1/install_payroll_phase1.py
```

It prints a report. Read it:

- **New files copied**: about 19 files.
- **Existing files edited: 4**
  - `config/settings/base.py` adds `"apps.payroll"` to `LOCAL_APPS`.
  - `apps/web/urls/__init__.py` includes the payroll pages.
  - `apps/web/templates/web/base.html` adds the Payroll sidebar link.
  - `apps/web/templates/web/staff/detail.html` adds the Pay card.
- **DO THESE BY HAND**: only appears if a place to edit was not found. It then prints the exact lines and which file to put them in.
- **Files that differ ... saved as `.payroll1`**: only appears if a file with the same name already exists and is different. The installer never overwrites your files. If you see this, send me the list.

It is safe to run twice. The second run says everything is already in place.

## Step 4: check that Django agrees with the migration

```bash
python manage.py makemigrations --check --dry-run
```

Expected: `No changes detected`. If it says it wants to create a payroll migration, stop and send me the output.

## Step 5: create the tables

```bash
python manage.py migrate
```

Expected line: `Applying payroll.0001_initial... OK`.

## Step 6: run the tests

```bash
python manage.py test apps.payroll apps.web.tests.test_payroll
```

Expected: `Ran 54 tests ... OK`. Then the whole suite, to be sure nothing else moved:

```bash
python manage.py test
```

If anything fails, copy the failure text and send it to me.

## Step 7: give the rights

Start the server (`python manage.py runserver`) and sign in as the superuser. The superuser sees **Payroll** straight away.

For HR staff: **Settings > Access**, then tick these for the right group:

| Right | What it allows |
|---|---|
| See staff salaries and payment details | Opens the Payroll pages and shows amounts and IBANs |
| Set salaries and payment details | New salaries and payment details |
| Correct the latest salary | Fix a mistake in place |
| See allowance types | The allowance list |
| Add and change allowance types | Add, rename, switch on or off |

Salaries are sensitive, so consider giving the first two to as few groups as possible.

## Step 8: try it by hand once

1. **Settings > Payroll**: press **Add** and create the client's allowance types (housing, transport, whatever appears on their payslips). The SIO tick can stay off until the accountant answers.
2. **Payroll**: you should see a yellow "N staff without a salary" button.
3. Open a staff member, then **Set salary**. The start date offers the joining date. Enter basic salary and allowances, and save.
4. Open the same person again and choose **Change salary**. The old figures are ready to edit. Pick a later date, save, and check the history shows the old salary ending the day before.
5. Try **Correct latest** with and without the reason.
6. **Payment**: save cash for one person. For another, bank transfer with an IBAN. Change one digit of the IBAN and confirm it is refused.
7. **Staff > any employee**: the Pay card appears under the documents card.
8. Check that a **contractor-supplied** worker is not listed, and a **direct** labor worker is, with "Pay terms under Labor".
9. Open the **audit log** and look at a salary event: the amounts should show as hidden.

## Step 9: commit

```bash
git status
git add -A
git commit -m "Payroll phase 1: staff salary structure, allowance types, payment details"
```

`git status` should show only `apps/payroll`, the payroll files in `apps/web`, and the four edited files. If you see `.payroll1` files, deal with them first.

---

## If something goes wrong

| What you see | What to do |
|---|---|
| `STOP: Run this from the Ivhrms project folder` | `cd /c/Django/Projects/Ivhrms` and run it again |
| `No module named 'apps.payroll'` | The settings edit was not made. Add `"apps.payroll",` to `LOCAL_APPS` in `config/settings/base.py` |
| `NoReverseMatch ... payroll_home` | The URL edit was not made. Add `from . import payroll` and `*payroll.urlpatterns,` in `apps/web/urls/__init__.py` |
| No Payroll link in the sidebar | The person has no payroll rights yet (Step 7), or the sidebar edit was not made |
| No Pay card on the staff page | Same two causes, for `staff/detail.html` |
| A test fails | Send me the failure text |

To undo everything before merging: `python manage.py migrate payroll zero`, then `git checkout main`, then `git branch -D payroll-phase1`.

---

## Things to confirm with the client

- The exact **allowance names** they pay.
- Whether each allowance **counts towards the SIO wage** (ask the accountant, it only matters for Bahraini staff). Phase 2 will use the tick.
- How each person is **paid** (bank or cash) and the bank details for the bank-paid ones.

## What comes next (phase 2)

- **Nationality** as a proper country list. SIO applies to Bahraini staff, and Air Tickets needs it too.
- A **dated payroll policy per company**: the days-in-month divisor (30) and the SIO rates. The accountant should supply the current rates.
- The **payroll run**: separate staff and labor runs per company per month, reviewed, approved, then frozen. Once a run uses a salary, **Correct latest** will refuse it, and a change goes through a new dated salary.
