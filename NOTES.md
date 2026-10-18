# Staff attendance grid, weekly off and holidays

## What you get

### 1. The attendance grid (Staff > Attendance)
- **Day, Week or Month** buttons, with Previous, This week/month and Next. It opens on the **week** (Sunday to Saturday).
  A setting under Settings > Attendance changes which period it opens on.
- **People down the side, days across.** A recorded day is a locked letter. An empty past day is a dashed box you click:
  blank, **P** present, **A** absent, **&frac12;** half day. You can also type P, A or H, press Backspace to clear, and move
  with the arrow keys.
- **The month fits the screen.** The boxes shrink (about 27 pixels each on a laptop, letters only) and the name column
  stays in place; a very narrow screen scrolls sideways. The week view has big boxes.
- **Quick ways:** "Give every empty working day: Present / Half day / Absent" at the top (you then type only the
  exceptions; the choice is previewed faintly in the boxes), a **P** under each working day to give that day to everyone,
  and a **P** at the end of each row to give that person every empty working day. These never touch a day you already
  chose, a recorded day, a weekly off or a holiday.
- **Day view** adds In and Out time boxes (the only place times are entered, so lateness is worked out there).
- Future days have no box. Someone who joined mid-period has blank days before joining.
- **Review and confirm comes first.** Nothing is saved until you see the review: totals, a line per person, anything that
  will be left out and why, and the rest days that will be recorded. Buttons: Confirm and save, Back to edit.

### 2. Weekly offs and holidays
- **Weekly off is a setting** (Settings > Holidays and weekly off > Weekly off). Friday is the default. You choose the
  days and the date they start; earlier days keep the weekly off they had.
  Order of precedence for a day: **a holiday, then the weekly off of the person's shift, then the company's weekly off,
  then Friday.** A person on a shift follows the shift's own days (a shift created earlier has Friday written in it; edit
  the shift if it should change).
- **Public holidays page** (Settings > Holidays and weekly off > Holidays): a list by year, add a day or a range such as
  a four-day Eid, for every company (superusers) or one, in English and Arabic, rename, switch off and on. Adding,
  renaming or switching off a holiday never changes attendance already recorded.
- **Rest days are recorded as attendance rows**, so payroll sees them as paid days:
  - the daily job (below) records them for everyone with no entry; and
  - when you **confirm** a grid, any weekly offs and holidays in that period that have no record yet are recorded too,
    and the review says so ("also recorded as rest days: 8 weekly offs, 2 holidays"). So a month-end catch-up works
    even if the job missed days.
- **Someone worked on a rest day or holiday:** on the grid, a recorded weekly off or holiday shows "Off" / "Hol" and can
  be given Present or Half day directly. Only the **system's** rest-day row can be replaced this way; a row a person
  entered still needs a correction that the manager approves. It is audited, and the review counts it.

## Rights
- Staff attendance needs `attendance.add_attendance` (Settings > Access: "Give attendance for staff").
- Holidays and weekly off: HR gets view, add and change on holidays and view and add on the weekly off; Management gets
  view only. This is given once when you migrate, so anything you change later on the Access tab is kept.
- The two Settings tabs appear for people with the Settings view right for them (Settings > Access). Give HR "Attendance"
  and "Holidays and weekly off" there. The pages themselves are also reachable by their own rights.

## Install

Install the attendance calendar first.

```
cd /c/Django/Projects/Ivhrms
git add -A && git commit -m "attendance calendar"          # so this step can be undone on its own
source ivenv/Scripts/activate
python /c/Temp/staff_attendance/install.py
python manage.py migrate
python manage.py test apps.scheduling apps.attendance apps.web
```
Send me any failure with the full message. If the installer says MANUAL, it tells you the exact line to add.
The installer also creates the migration for the weekly-off rule (one new table; no existing data is touched).

## Schedule the daily job (so rest days are recorded without anyone opening the grid)

`fill_attendance` already exists. Run it every morning, a little after midnight:

- **Windows Task Scheduler:** Create Basic Task > Daily at 00:10 > Start a program:
  - Program: `C:\Django\Projects\Ivhrms\ivenv\Scripts\python.exe`
  - Arguments: `manage.py fill_attendance`
  - Start in: `C:\Django\Projects\Ivhrms`
- To fill one date by hand: `python manage.py fill_attendance --date 2026-10-16`

It is safe to run twice. The grid's confirmation covers anything the job missed.

## Check it by hand (10 minutes)

1. **Weekly off.** Settings > Holidays and weekly off shows Friday for each company. Open "Weekly off", choose Friday
   and Saturday starting on the 1st of next month, save. The company's list shows both rules with their dates. Open the
   leave calendar of an employee: the shading follows.
2. **Holidays.** Add a one-day holiday and a three-day one. Try the same day again: it refuses and names the date. Rename
   one, switch one off and on.
3. **Grid, week.** Staff > Attendance. Choose "Give every empty working day: Present" and type one Absent. Press Review
   and confirm: totals, a line per person, "also recorded as rest days: ...". Press Back to edit (choices kept), then
   Confirm and save. The grid now shows locked letters and "Off" on Fridays.
4. **Worked on a Friday.** Click an "Off" box twice (or type P): it becomes P. Review says it replaces a weekly off;
   confirm. That day is now Present.
5. **Month.** Press Month: the whole month fits the screen, with small boxes. Use a **P** at the end of a row.
6. **Day.** Press Day: In and Out boxes appear. Give a late check-in: the saved day shows late.
7. **A recorded day** cannot be changed here: it has no box.
8. **Settings > Attendance:** change the period the sheet opens on to Month; the grid opens on the month.

## Not included, on purpose
- Compensatory leave for working a rest day: a policy decision for later.
- Making every shift follow the company weekly off automatically (shifts keep their own days).
- Labour attendance (the labor site sheet already does it, with project and site).

## If you need to go back
```
python manage.py migrate scheduling <the number before weekly_off_rule>
git checkout .
git clean -fd apps/
```
The old files are also kept as `.bak` next to the new ones.
