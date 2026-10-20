# Ivhrms: build checklist

Last updated: 20 October 2026. Project folder: `C:\Django\Projects\Ivhrms`.

How to read it: `[x]` is built and delivered. `[ ]` is still to do. The last part, "Parked", is out of the plan for now
and is kept here so nothing is forgotten.

---

## 1. Delivered packages and change lists: confirm each one is installed

Install in this order. After each one: `python manage.py migrate` (where it has a migration) and
`python manage.py test` for the apps it touches. Every package has its own `NOTES.md` with a hand-check.

- [ ] `family_eligibility`: designation tick for family visa, per-person decisions, add family from the employee page
- [ ] `family_status`: family status requests (employee or HR asks, HR sends on, manager approves), My family page
- [ ] `users_access`: Settings > Users (create logins, groups, companies, reset password), linked to Access
- [ ] `attendance_calendar`: My attendance as a calendar with a day panel and correction requests
- [ ] `staff_attendance`: staff attendance grid (day, week, month), weekly off setting, public holidays page,
      one Attendance tab in Settings
- [ ] `status_approver`: one named person approves every family status request of a company

Change lists given by hand (no package):

- [ ] Usernames: choose when creating, rename later, no duplicates, live availability (`username_changes.md`)
- [ ] Documents: registration date, Responsible is a staff member (searchable, labour excluded, name only)
- [ ] Documents: opening the add form from a list fixes the person and the type, Cancel and Save go back to where you came from
- [ ] Staff page layout: reporting-manager warning moved into "Reports to", Family card as a compact card
- [ ] `staff_sheet.html` fresh copy (period label beside the count, search box, department list, Clear all)

Then:

- [ ] Run the whole test suite once: `python manage.py test`
- [ ] Delete the `.bak` files the installers left, once you are happy
- [ ] Delete two unused files: `templates/web/settings/_scheduling_weekly_off.html` and `_scheduling_holidays.html`

---

## 2. Built

### People and records

- [x] Employee master record, department, designation, reporting manager, joining date, status
- [x] History of transfers and promotions (dated assignments)
- [x] Staff list with search and filters
- [x] Documents: types, versions, issue and expiry dates, email reminders, renewal tasks, fees and payments, cost report, audit
- [x] Web logins, Users page, groups and companies per person, Access page linked to the people in each group
- [x] Audit log on records and decisions

### Employee self-service

- [x] Dashboard
- [x] My leave (apply, balances, history)
- [x] My attendance (calendar, day panel, correction requests with approval)
- [x] My air ticket (see and request)
- [x] My family (family status, request progress, family members, files to view and download, read-only)
- [x] Approvals inbox for managers

### Family visas

- [x] Family members, papers, visa requests with approval, costs, expiry alerts
- [x] Family status: HR or the employee asks, HR sends on or declines, the manager (or one named person) decides
- [x] Nobody has family status by default; managers get it from the designation tick
- [x] One named approver per company (for example the owner), with the self-approval cases explained

### Attendance, shifts, holidays

- [x] Statuses, corrections with approval, audit
- [x] Staff attendance grid: day, week or month, fill the empty days, review and confirm before saving
- [x] Weekly off as a dated company setting (Friday by default); a shift can keep its own days
- [x] Public holidays page (a day or a range, one company or all)
- [x] Weekly offs and holidays recorded as attendance rows (daily job, and when a grid is confirmed)
- [x] A worked weekly off or holiday can be given Present or Half day by HR
- [x] Shift rules: hours, breaks, grace period, history of changes (data and rules)

### Leave

- [x] Leave types, balances, apply, approve, leave calendar hints

### Labor

- [x] Labor profiles, trade, contractor or direct, dated pay rates
- [x] Sites, projects, work orders, allocation and moves
- [x] Site attendance sheets and timesheets, confirm and reopen
- [x] Overtime: rules, claims, approval
- [x] Reports: deployment, manpower, cost, contractor statement, timesheet (Excel)

### Payroll and exit

- [x] Salary records and allowances, payment details
- [x] Payroll runs for staff and direct labor, adjustments, SIO, review and approval
- [x] Payslips (for HR), payroll register, bank transfer sheet, cash list
- [x] Final settlement: notice, leave payout, end-of-service and SIO gratuity, statement, certificate, approval, payment

### Also built (not in the requirements document)

- [x] Air tickets: policies, eligibility, requests, reminders, reports
- [x] Vehicles: register, assignment, usage, upkeep, incidents, fines, custody, reports

---

## 3. Your own tasks (setup, nothing to build)

- [ ] Schedule the daily job in Windows Task Scheduler: `manage.py fill_attendance`, daily at 00:10
- [ ] Settings > Access: tick the Attendance rights for HR (see the settings, change them, give attendance, holidays, weekly off)
- [ ] Settings > Access: tick "Decide who may have a family visa" rights as you wish for HR and Management
- [ ] Organization > Designations: tick "Family visa allowed by default" for the manager-level designations and for the owner's
- [ ] Settings > Family Visas > Who approves family status: name the owner for the company
- [ ] Give each member of staff who should receive document alerts a login and an email address
- [ ] Set reporting managers where the approvals need them
- [ ] Add this year's public holidays (Settings > Attendance), and review them each year: Islamic dates move
- [ ] Check the weekly off for each company, and change it from a date if it ever changes
- [ ] Fill the payroll policy for each company before the first run

---

## 4. Parked: out of the plan for now

You decided to leave these out. Tick them off here if they come back.

### Modules in the requirements document that are not started

- [x] Loan Management (loans and salary advances, EMI through payroll, pre-closure, balance at exit)
- [ ] Child Education Benefits (eligibility, yearly limits, fee claims, approval, reimbursement)
- [ ] Disciplinary Management (incidents, warning letters, show-cause notices, hearings, history)
- [ ] Asset Management (register, issue and return with acknowledgment, maintenance, exit clearance)
- [ ] Insurance Management (medical and life policies, dependants' cover, claims, renewals)
- [ ] Food Allowance rules (daily or monthly, exclusions, proration; today it is a fixed allowance)

### Built only in part

- [ ] Employee self-service: payslips and tax documents, update personal and bank details, profile-change requests,
      upload documents, announcements, requests for loans, permission, overtime and business travel, employment letters
- [ ] Shift screens: define shifts and assign them to groups (today only in Django admin)
- [ ] Staff timesheets with projects and tasks (labor has them)
- [ ] Staff overtime: weekday, weekend and holiday rates, approval, payroll (labor has it)
- [ ] Reports: Employee, Attendance, Leave, staff Timesheet, Overtime, and a Payroll report across periods
- [ ] Final settlement: tracked clearance by department (today a fixed checklist), experience certificate
- [ ] Check: leave entitlement and carry-forward settings screen, and leave reports
- [ ] Check: approval step for document renewals

### From our own work

- [ ] Manager approval switch for family visa requests (today every request follows the approval flow)
- [ ] Follow-up list for visa requests waiting with the PRO
- [ ] Emergency contact for each employee
- [ ] Family air tickets that depend on family status
- [ ] Compensatory leave for working a rest day
- [ ] Let every shift follow the company weekly off automatically
- [ ] HR view of one person's attendance calendar; selecting several days at once for a correction

### Small fixes

- [ ] Side menu: "My air ticket" is labelled "My leave"
- [ ] Side menu: add a "My family" entry (today it is a button on the dashboard)

---

## 5. Decisions we made (so they are not reopened by accident)

- Family details and new family visa requests need family status. Renewals and cancellations of an existing visa never ask.
- Family status comes from an approved request or the designation tick; HR can remove it, not give it by hand.
- An employee's own family status request goes to HR first; HR's own request goes straight to the approver.
- Nobody approves their own request, or one they raised.
- Weekly off: a holiday wins, then the person's shift, then the company's dated rule, then Friday.
- Rest days are recorded as attendance rows because payroll counts them as paid days.
- An attendance entry that exists is never overwritten on the grid; changes need a correction. Only the system's
  weekly-off or holiday row can be replaced when someone worked.
- Labour is not listed as a Responsible person for documents; staff only, by name.
- Attendance statuses are Present, Absent, Half day (and the system's own); hours are for the labor timesheet only.
