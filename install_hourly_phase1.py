"""Ivhrms labor pay terms, phase 1: model, forms and migration.

Run from the project root (the folder with manage.py), with ivenv active:
    python install_hourly_phase1.py

What it does
  1. Edits 6 existing files (all edits are checked first; if any anchor is missing, NOTHING is written).
  2. Runs makemigrations for the labor app.
  3. Adds a data migration so existing rates keep their behaviour
     (premium copied from overtime, holidays unpaid).
"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path.cwd()
problems, pending = [], {}


def load(rel):
    path = ROOT / rel
    if not path.exists():
        problems.append(f"{rel}: file not found")
        return None, None, False
    raw = path.read_bytes().decode("utf-8")
    return path, raw.replace("\r\n", "\n"), "\r\n" in raw


def edit(rel, pairs):
    path, text, crlf = load(rel)
    if path is None:
        return
    for old, new in pairs:
        if text.count(old) != 1:
            problems.append(f"{rel}: expected 1 match, found {text.count(old)} for: {old[:70]!r}")
        else:
            text = text.replace(old, new)
    pending[path] = (text, crlf)


# ---------------------------------------------------------------- apps/labor/models.py
CONTRACTOR_FIELDS = '''    # Pre-fill for the workers this contractor supplies. Empty means HR must choose for every worker.
    default_overtime = models.BooleanField(
        "Overtime by default", null=True, blank=True,
        help_text="What the worker form starts with for this contractor's workers. Leave empty to choose each time.")
    default_weekend_premium = models.BooleanField(
        "Friday and holiday premium by default", null=True, blank=True,
        help_text="What the worker form starts with for this contractor's workers. Leave empty to choose each time.")
'''

RATE_FIELDS = '''    overtime_eligible = models.BooleanField("Overtime", default=True)
    weekend_premium = models.BooleanField(
        "Friday and holiday premium", default=False,
        help_text="Yes: Fridays (the weekly off) and public holidays are paid at the company's premium multiplier. "
                  "No: those hours are paid like any other day.")
    paid_holidays = models.BooleanField(
        "Paid for holidays and paid leave", default=False,
        help_text="Yes: public holidays and paid leave are paid at the standard hours even if not worked.")
'''

path, text, crlf = load("apps/labor/models.py")
if path:
    for old, new in [
        ('        MONTHLY = "monthly", "Monthly salary"\n',
         '        MONTHLY = "monthly", "Monthly salary"\n        HOURLY = "hourly", "Hourly"\n'),
        ('help_text="Per day for a daily wage, per month for a monthly salary.")',
         'help_text="Per hour, per day or per month, depending on the pay basis.")'),
        ('    overtime_eligible = models.BooleanField(default=True)\n', RATE_FIELDS),
    ]:
        if text.count(old) != 1:
            problems.append(f"apps/labor/models.py: expected 1 match, found {text.count(old)} for: {old[:70]!r}")
        else:
            text = text.replace(old, new)
    start = text.find("class Contractor(")
    anchor = "    is_active = models.BooleanField(default=True)\n"
    if start < 0:
        problems.append("apps/labor/models.py: class Contractor not found")
    else:
        nxt = text.find("\nclass ", start + 5)
        pos = text.find(anchor, start)
        if pos < 0 or (0 < nxt < pos):
            problems.append("apps/labor/models.py: is_active line not found inside Contractor")
        else:
            pos += len(anchor)
            text = text[:pos] + CONTRACTOR_FIELDS + text[pos:]
    pending[path] = (text, crlf)

# ---------------------------------------------------------------- apps/labor/services.py
edit("apps/labor/services.py", [
    ('raise LaborError("Choose a daily wage or a monthly salary.")',
     'raise LaborError("Choose hourly, daily or monthly pay.")'),
    ('if engagement == Engagement.CONTRACTED and wage_basis != WageBasis.DAILY:\n'
     '        raise LaborError("Contracted workers are paid a daily rate.")',
     'if engagement == Engagement.CONTRACTED and wage_basis == WageBasis.MONTHLY:\n'
     '        raise LaborError("Contracted workers are paid a daily rate or an hourly rate, not a monthly salary.")'),
    ('def _open_profile(employee, *, engagement, contractor, trade, notes, wage_basis, rate, standard_hours,\n'
     '                  overtime_eligible, effective_from):',
     'def _switches(overtime_eligible, weekend_premium, paid_holidays):\n'
     '    # Callers that do not pass the new switches keep the old behaviour: premium follows overtime, holidays unpaid.\n'
     '    return (overtime_eligible,\n'
     '            overtime_eligible if weekend_premium is None else weekend_premium,\n'
     '            False if paid_holidays is None else paid_holidays)\n'
     '\n\n'
     'def _open_profile(employee, *, engagement, contractor, trade, notes, wage_basis, rate, standard_hours,\n'
     '                  overtime_eligible, effective_from, weekend_premium=None, paid_holidays=None):'),
    ('    LaborRate.objects.create(profile=profile, wage_basis=wage_basis, rate=rate, standard_hours=standard_hours,\n'
     '                             overtime_eligible=overtime_eligible, effective_from=effective_from)\n'
     '    return profile',
     '    overtime_eligible, weekend_premium, paid_holidays = _switches(overtime_eligible, weekend_premium, paid_holidays)\n'
     '    LaborRate.objects.create(profile=profile, wage_basis=wage_basis, rate=rate, standard_hours=standard_hours,\n'
     '                             overtime_eligible=overtime_eligible, weekend_premium=weekend_premium,\n'
     '                             paid_holidays=paid_holidays, effective_from=effective_from)\n'
     '    return profile'),
    ('def create_labor_worker(employee, *, engagement, contractor=None, trade, notes="", wage_basis, rate,\n'
     '                        standard_hours=Decimal("8"), overtime_eligible=True):',
     'def create_labor_worker(employee, *, engagement, contractor=None, trade, notes="", wage_basis, rate,\n'
     '                        standard_hours=Decimal("8"), overtime_eligible=True, weekend_premium=None,\n'
     '                        paid_holidays=None):'),
    ('overtime_eligible=overtime_eligible, effective_from=employee.joining_date)',
     'overtime_eligible=overtime_eligible, effective_from=employee.joining_date,\n'
     '                         weekend_premium=weekend_premium, paid_holidays=paid_holidays)'),
    ('def setup_profile(employee, *, engagement, contractor=None, trade, notes="", wage_basis, rate,\n'
     '                  standard_hours=Decimal("8"), overtime_eligible=True, effective_from=None):',
     'def setup_profile(employee, *, engagement, contractor=None, trade, notes="", wage_basis, rate,\n'
     '                  standard_hours=Decimal("8"), overtime_eligible=True, effective_from=None,\n'
     '                  weekend_premium=None, paid_holidays=None):'),
    ('overtime_eligible=overtime_eligible, effective_from=effective_from or employee.joining_date)',
     'overtime_eligible=overtime_eligible, effective_from=effective_from or employee.joining_date,\n'
     '                         weekend_premium=weekend_premium, paid_holidays=paid_holidays)'),
    ('if engagement == Engagement.CONTRACTED and current and current.wage_basis != WageBasis.DAILY:',
     'if engagement == Engagement.CONTRACTED and current and current.wage_basis == WageBasis.MONTHLY:'),
    ('def change_rate(profile, *, effective_from, wage_basis, rate, standard_hours, overtime_eligible):',
     'def change_rate(profile, *, effective_from, wage_basis, rate, standard_hours, overtime_eligible,\n'
     '                weekend_premium=None, paid_holidays=None):'),
    ('    return LaborRate.objects.create(profile=profile, wage_basis=wage_basis, rate=rate, standard_hours=standard_hours,\n'
     '                                    overtime_eligible=overtime_eligible, effective_from=effective_from)',
     '    overtime_eligible, weekend_premium, paid_holidays = _switches(overtime_eligible, weekend_premium, paid_holidays)\n'
     '    return LaborRate.objects.create(profile=profile, wage_basis=wage_basis, rate=rate, standard_hours=standard_hours,\n'
     '                                    overtime_eligible=overtime_eligible, weekend_premium=weekend_premium,\n'
     '                                    paid_holidays=paid_holidays, effective_from=effective_from)'),
])

# ---------------------------------------------------------------- apps/labor/overtime_services.py
edit("apps/labor/overtime_services.py", [
    ('    per_day = rate.rate if rate.wage_basis == LaborRate.WageBasis.DAILY else rate.rate / policy.monthly_divisor',
     '    if rate.wage_basis == LaborRate.WageBasis.HOURLY:         # an hourly rate already is the hour\n'
     '        return rate.rate.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)\n'
     '    per_day = rate.rate if rate.wage_basis == LaborRate.WageBasis.DAILY else rate.rate / policy.monthly_divisor'),
])

# ---------------------------------------------------------------- apps/web/forms/labor.py
edit("apps/web/forms/labor.py", [
    # no pre-selected overtime choice any more: HR must pick
    (',\n            "overtime_eligible": settings_service.get("labor.new_worker_overtime_eligible")}', '}'),
    ('class _EngagementForm(forms.Form):',
     'def _yes_no(label, help_text=""):\n'
     '    # Radio buttons with nothing pre-selected, so HR has to choose and nobody gets the wrong terms by accident.\n'
     '    return forms.TypedChoiceField(choices=[("1", "Yes"), ("0", "No")], coerce=lambda v: v == "1",\n'
     '                                  widget=forms.RadioSelect, label=label, help_text=help_text,\n'
     '                                  error_messages={"required": "Choose Yes or No."})\n'
     '\n\n'
     'def _yn(value):\n'
     '    return "1" if value else "0"\n'
     '\n\n'
     'class _EngagementForm(forms.Form):'),
    ('if engagement == "contracted" and cd.get("wage_basis") not in (None, DAILY):',
     'if engagement == "contracted" and cd.get("wage_basis") == "monthly":'),
    ('self.add_error("wage_basis", "Contracted workers are paid a daily rate.")',
     'self.add_error("wage_basis", "Contracted workers are paid hourly or by the day.")'),
    ('help_text="Per day for a daily wage, per month for a monthly salary.")',
     'help_text="Per hour, per day or per month, depending on the pay basis.")'),
    ('    overtime_eligible = forms.BooleanField(required=False, initial=True, label="Eligible for overtime")',
     '    overtime_eligible = _yes_no("Overtime", "Yes: hours beyond the standard day are paid at the overtime multiplier. "\n'
     '                                             "No: every hour is paid at the normal hourly rate.")\n'
     '    weekend_premium = _yes_no("Friday and holiday premium",\n'
     '                              "Yes: Fridays and public holidays are paid at the company premium multiplier.")\n'
     '    paid_holidays = _yes_no("Paid for holidays and paid leave",\n'
     '                            "Yes: public holidays and paid leave are paid at the standard hours.")'),
    ('standard_hours=current.standard_hours, overtime_eligible=current.overtime_eligible)',
     'standard_hours=current.standard_hours, overtime_eligible=_yn(current.overtime_eligible),\n'
     '                                weekend_premium=_yn(current.weekend_premium),\n'
     '                                paid_holidays=_yn(current.paid_holidays))'),
    ('            self.fields["wage_basis"].choices = [(DAILY, "Daily wage")]\n'
     '            self.initial["wage_basis"] = DAILY',
     '            self.fields["wage_basis"].choices = [c for c in LaborRate.WageBasis.choices\n'
     '                                                 if c[0] != LaborRate.WageBasis.MONTHLY]\n'
     '            if self.initial.get("wage_basis") not in (DAILY, LaborRate.WageBasis.HOURLY):\n'
     '                self.initial["wage_basis"] = DAILY'),
    ('class ContractorForm(forms.ModelForm):',
     '_DEFAULT_CHOICES = [("", "No default (HR chooses each time)"), ("True", "Yes"), ("False", "No")]\n'
     '\n\n'
     'class ContractorForm(forms.ModelForm):'),
    ('fields = ["company", "name", "name_ar", "cr_number", "contact_person", "phone", "email", "address", "notes"]',
     'fields = ["company", "name", "name_ar", "cr_number", "contact_person", "phone", "email", "address", "notes",\n'
     '                  "default_overtime", "default_weekend_premium"]'),
    ('widgets = {"address": forms.Textarea(attrs={"rows": 2}), "notes": forms.Textarea(attrs={"rows": 2})}',
     'widgets = {"address": forms.Textarea(attrs={"rows": 2}), "notes": forms.Textarea(attrs={"rows": 2}),\n'
     '                   "default_overtime": forms.Select(choices=_DEFAULT_CHOICES),\n'
     '                   "default_weekend_premium": forms.Select(choices=_DEFAULT_CHOICES)}'),
])

# ---------------------------------------------------------------- apps/web/views/labor.py (three service calls)
path, text, crlf = load("apps/web/views/labor.py")
if path:
    old = 'overtime_eligible=cd["overtime_eligible"]'
    new = ('overtime_eligible=cd["overtime_eligible"], weekend_premium=cd["weekend_premium"], '
           'paid_holidays=cd["paid_holidays"]')
    if text.count(old) != 3:
        problems.append(f"apps/web/views/labor.py: expected 3 service calls, found {text.count(old)}")
    else:
        pending[path] = (text.replace(old, new), crlf)

# ---------------------------------------------------------------- stop here if anything is off
if problems:
    print("NOTHING WAS CHANGED. These edits could not be placed:\n")
    for p in problems:
        print("  -", p)
    print("\nSend me this list and I will adjust the script.")
    sys.exit(1)

for path, (text, crlf) in pending.items():
    path.write_bytes((text.replace("\n", "\r\n") if crlf else text).encode("utf-8"))
    print("edited", path.relative_to(ROOT))

# ---------------------------------------------------------------- migrations
mig_dir = ROOT / "apps/labor/migrations"
before = {p.name for p in mig_dir.glob("0*.py")}
r = subprocess.run([sys.executable, "manage.py", "makemigrations", "labor", "-n", "pay_switches"], cwd=ROOT)
if r.returncode:
    print("makemigrations failed: the files above are already edited; fix the error and run it by hand:")
    print("  python manage.py makemigrations labor -n pay_switches")
    sys.exit(1)
new = sorted({p.name for p in mig_dir.glob("0*.py")} - before)
if len(new) != 1:
    print("Expected one new migration, found:", new, "- add the data migration by hand (see the notes).")
    sys.exit(1)
schema_name = new[0][:-3]
number = int(schema_name[:4]) + 1
data_file = mig_dir / f"{number:04d}_copy_premium_from_overtime.py"
data_file.write_text(f'''from django.db import migrations, models


def copy_premium(apps, schema_editor):
    # Existing rates keep their behaviour: the premium follows their overtime setting, holidays stay unpaid.
    LaborRate = apps.get_model("labor", "LaborRate")
    LaborRate.objects.update(weekend_premium=models.F("overtime_eligible"), paid_holidays=False)


class Migration(migrations.Migration):

    dependencies = [("labor", "{schema_name}")]

    operations = [migrations.RunPython(copy_premium, migrations.RunPython.noop)]
''', encoding="utf-8")
print("created", data_file.relative_to(ROOT))
print("\nDone. Next: python manage.py migrate && python manage.py test apps.labor")
