# Loans and salary advances (Payroll > Loans): step one, the HR side

## What you get

A **Loans** tab in Payroll (between Payroll runs and Final settlements), for staff and for labor.

1. **One list.** Number, employee, amount, repaid, balance, next deduction, status. Filter by status, search by name or
   number. Open loans show by default.
2. **New loan.** Press New loan, search the person (2 letters), then fill the form:
   - **Loan or salary advance** (an advance is one instalment, taken at the next payroll).
   - **Amount, instalments, first deduction month, reason.**
   - **Yearly interest %**, 0 to start. It is flat, on the amount, spread equally over the instalments. With 0 nothing
     changes. The default comes from Settings; HR can change it on one loan.
   - A live **schedule** under the form ("12 instalments of 83.333, the last one 83.337"). The last instalment takes the
     rounding, so the instalments always add up exactly.
3. **How it was agreed**, two ways:
   - **Send for approval**: HR prepares it, sends it, Management approves it in the Approvals inbox (the same as a final
     settlement). A rejected or withdrawn loan goes back to a draft with the reason.
   - **Already agreed verbally**: for a small firm where Management says yes in person. HR records **who agreed and
     when**, and the loan starts as approved. It is shown on the loan ("Agreed verbally by Mr. X on 15 Oct 2026") and in the
     audit log, and it has **its own right**, so you decide who may use it.
4. **Pay out.** HR records the bank transfer: date and reference. The loan becomes Active.
5. **Payroll.** On the payroll run page, "Pick up charges" now also picks up the instalments due by the end of that month,
   next to fines and renewal fees ("Loan LN-0004, instalment 3 of 12"). They become deductions, and can be removed again.
   An instalment counts as repaid only when the run is **approved**; a cancelled run changes nothing; one instalment can
   never be deducted twice. A person is never picked up before the loan is paid out.
6. **On the loan page**: the amount, repaid, balance, next deduction; the schedule; the payments; who agreed it and the
   bank transfer. HR can:
   - **Record an early payment** (bank or cash): the last instalments are shortened or covered, so the monthly amount stays.
   - **Pay off the loan** (pre-closure): the whole balance, and the loan closes.
   - **Skip the next month**: that instalment is left out and the same amount is added after the last one.
   - **Reschedule the rest**: the balance is spread again over the months you choose. The old rows are kept.
   - **Waive the balance**, with a reason: it will not be recovered; the loan closes.
   - **Cancel** a draft, or an approved loan that was not paid out (with a reason). Print a **statement**.
7. **Final settlement.** The leaver's open loans appear as a line, "Loan LN-0004 balance", oldest first. The settlement can
   only pay what it holds: if it is smaller than the balance, the line takes what there is, the rest **stays on the loan**
   and the case shows "X BHD is still owed" (the settlement is never made negative). On approval the amount counts as
   repaid. To drop the rest, open the loan and waive it.
8. **Limits (optional).** Settings > Payroll > Loans: default interest, largest loan in monthly salaries, most instalments,
   largest share of a month's pay. **0 means no limit**, and all start at 0. If you fill one in and a loan goes over it, HR sees
   a clear warning and must give a reason to go ahead; the reason is kept on the loan.
9. **Not allowed:** a loan for someone who has left or who already has a final settlement; changing an approved loan's amount.

## Rights (Settings > Access, group Payroll)

See loans; Prepare loans; Send a loan for approval; Record payouts and repayments; Add a loan agreed verbally.
HR gets all five and Management gets "see", given once when you migrate, so anything you change later is kept.

## Install

Install the payroll runs and final settlements first (they are already there).

```
cd /c/Django/Projects/Ivhrms
git add -A && git commit -m "before loans"                  # so this step can be undone on its own
source ivenv/Scripts/activate
python /c/Temp/payroll_loans/install.py
python manage.py migrate
python manage.py test apps.payroll apps.web
```
Send me any failure with the full message. If the installer says MANUAL, it tells you the exact lines to change.
`makemigrations` makes one migration: three new tables, and "loan" added to two lists of choices (no existing data changes).

## Check it by hand (10 minutes)

1. Payroll has a **Loans** tab. Settings > Payroll shows a **Loans** section; Settings > Access shows five loan rights.
2. **New loan**, search a person, 1000 BHD, 12 months, first month next month. The preview says "12 instalments of 83.333,
   the last one 83.337". Create. It is a draft.
3. **Send for approval**. Sign in as Management: Approvals shows "Loan"; approve. The loan is "Approved, not paid out".
4. **Record the bank transfer** (a reference is required). It is Active, with the 12 instalments.
5. Open the payroll run for the month. **Pick up charges** shows the instalment. Add it, approve the run: the loan page
   shows the instalment as Deducted, repaid 83.333, balance 916.667.
6. **Record an early payment** of 250: the last instalments are covered, balance 666.667. **Skip the next month**, then
   **Reschedule the rest** over 4 months.
7. **New loan**, another person, choose **Already agreed verbally**, enter a name and a date: it starts as Approved.
8. Start a final settlement for the person with an active loan: the loan balance is a line. Try a loan bigger than the
   settlement: the warning says how much is still owed, and the loan stays Active after approval.
9. In Settings, set "Most instalments" to 6, then create a 12-instalment loan: the warning appears and a reason is needed.
10. Sign in as someone with only "See loans": they can read, never change.

## Step two (not built yet, as agreed)

The employee's **My loans** page (balance, schedule, and a request form) with an **on/off switch in Settings**, off to
start; and a loans report (who owes what, and the deductions due next month).

## If you need to go back

```
python manage.py migrate payroll <the number before loans>
git checkout .
git clean -fd apps/
```
