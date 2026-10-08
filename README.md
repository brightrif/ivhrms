# Labor reports: deployment, manpower, cost and contractor statement, each with Excel export

Needs Labor phases 1 to 5, the overtime-decision update and the Settings page installed first. No migration.

## Install (on a new git branch)

    git checkout -b labor-reports
    python install_reports.py             # run from the project root, the folder with manage.py
    python manage.py makemigrations --check --dry-run     # should say: No changes detected
    python manage.py test apps.labor apps.web

The Excel files are written by the project itself; nothing needs to be pip-installed.

## The four reports (Labor > Reports)

Every report shows on screen and has an "Export to Excel" button that downloads exactly what the screen shows, with
the same choices (dates, grouping, filters).

- **Deployment:** who is allocated where on a date. By site (direct and contracted), by trade against sites, and by
  contractor against sites.
- **Manpower by day:** people who actually worked each day at each site, from attendance, with man-days.
- **Cost:** wages and overtime for a period, by site, work order, trade, contractor or worker, filtered by company and by
  direct or contracted. Overtime pending approval is shown apart and is not in the total.
- **Contractor statement:** amount payable for a contractor's workers (or for contracted workers the client engages
  directly): days worked x daily rate, plus approved overtime, by worker and by site and work order.

## How cost is worked out (an estimate, before payroll; also printed on every cost report)

- Daily-wage workers: days worked x the daily rate in force that day. A half day is half. Absence and leave are not paid.
- Monthly-salary workers: salary / days in a month (from the overtime rules, 30 if there are none) for every calendar
  day allocated to the site, except days marked absent or unpaid leave.
- A daily-wage worker's day on a weekly off or holiday counts as a day worked but is paid through overtime when the
  company's rules count every hour on a day off as overtime, so it is not paid twice.
- Total = wages + approved overtime. Rejected overtime is ignored.

## Who sees what

Deployment and manpower need the right to see sites ("See sites, the deployment and manpower reports"). Cost and the
contractor statement need the right to see pay ("See pay rates, the cost reports and contractor statements"). Both are
on the Settings > Access grid, so the superuser decides which groups have them. Someone without the pay right is refused
the cost reports and their downloads and never sees a money column anywhere in the headcount reports.
