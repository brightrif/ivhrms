# Labor Management, phase 1

Contractors, trades, labor workers and their pay terms.

## Install (on a new git branch)

    git checkout -b labor-phase1
    python install_labor.py            # run from the project root, the folder with manage.py
    python manage.py migrate
    python manage.py makemigrations --check --dry-run     # should say: No changes detected
    python manage.py test apps.labor apps.web

The installer copies new files, makes three small edits (LOCAL_APPS, web/urls/__init__.py, the sidebar in base.html)
and never overwrites a file that already exists with different content. Running it twice changes nothing.

## What is in it

- apps/labor: Trade, Contractor, LaborProfile, LaborRate (effective-dated pay), services, group permissions (set up
  automatically after migrate), tests.
- apps/web: pages under /labor/ (workers, contractors, trades), forms, tests.

HR can add and change everything, and add new pay terms. Finance and Management can view. Pay is shown only to those
with the view_laborrate permission.
