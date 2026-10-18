# Staff attendance grid, weekly off and holidays: where each file belongs

Project folder: `C:\Django\Projects\Ivhrms` (Git Bash: `/c/Django/Projects/Ivhrms`).
`install.py` copies every file, makes the small edits and creates one migration. This table is only so you can see what
goes where, or do a MANUAL step.

## New files

| File | What it is |
|---|---|
| `apps/scheduling/weekly_off.py` | the company's weekly off as dated rules (a new table), and the HR / Management rights on the calendar |
| `apps/scheduling/calendar_rules.py` | reading the weekly off for a stretch of days; starting a new weekly off; adding, renaming and switching off holidays |
| `apps/scheduling/test_calendar_rules.py` | tests |
| `apps/attendance/restdays.py` | recording weekly offs and holidays as attendance rows; replacing one when someone worked |
| `apps/attendance/test_restdays.py` | tests |
| `apps/web/forms/scheduling_settings.py` | the holiday and weekly-off forms |
| `apps/web/views/scheduling_settings.py` | the Holidays and Weekly off pages |
| `apps/web/templates/web/settings/holidays.html`, `holiday_form.html`, `weekly_off.html` | those pages |
| `apps/web/templates/web/settings/_scheduling_weekly_off.html`, `_scheduling_holidays.html` | the two sections on the Settings tab |
| `apps/web/tests/test_scheduling_settings.py` | tests |

## Replaced (the old copy is saved as `.bak`)

| File | What changes |
|---|---|
| `apps/attendance/staffsheet.py` | day, week and month grid; the plan with rest-day catch-up; applying it |
| `apps/web/views/staff_attendance.py` | the grid page, the review and the confirmation |
| `apps/web/templates/web/attendance/staff_sheet.html`, `staff_review.html` | the grid and the review |
| `apps/web/tests/test_staff_attendance.py` | tests |
| `apps/web/urls/attendance.py` | the staff route beside the calendar's routes (unchanged from the last version) |

## Added only if the app has none yet

| File | What it is |
|---|---|
| `apps/scheduling/settings_spec.py` | the "Holidays and weekly off" tab of Settings and its rights |
| `apps/attendance/settings_spec.py` | the "Attendance" tab of Settings (the period the sheet opens on) and its right |

If one of them already exists, the installer leaves it alone and tells you what to add.

## Small edits made by the installer

| File | The change |
|---|---|
| `apps/scheduling/models.py` | one line at the end loads the weekly-off rule |
| `apps/scheduling/services.py` | `day_types` takes the company's weekly off for the day instead of the fixed default (3 small edits) |
| `apps/web/calendar_hints.py` | the leave calendar shades the same weekly off |
| `apps/web/urls/settings_hub.py` | the holiday and weekly-off routes, above the catch-all `settings/<slug:module>/` |
| `apps/web/templates/web/staff/_tabs.html` | the Attendance tab (if not there already) |
| `apps/web/templates/web/staff/detail.html` | the Mark attendance button (if not there already) |

## Created by `makemigrations`

| File |
|---|
| `apps/scheduling/migrations/NNNN_weekly_off_rule.py` |
