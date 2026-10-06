# apps/web

The web interface. The domain apps (leave, compliance, ...) hold the models and the rules;
this app only shows them.

    access.py        who may open a page: hr_perm, employee_required
    views/           one module per feature: dashboard, account, leave, approvals, attendance,
                     staff, companies, organization, compliance
    forms/           one module per feature, same names; common.py holds date_input and time_input
    urls/            one module per feature, joined in __init__.py (route names stay under "web:")
    tests/           test_<feature>.py
    templates/web/   base.html and the shared pages at the top; one folder per feature,
                     partials start with an underscore
    templatetags/    bs.py: form styling and status badges

Adding a feature (say "payroll"): views/payroll.py, forms/payroll.py, urls/payroll.py (list it in
urls/__init__.py), templates/web/payroll/ and tests/test_payroll.py.
