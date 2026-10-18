#!/usr/bin/env python
"""Install Staff attendance (the grid), the weekly-off setting and the holidays page into Ivhrms.

    cd /c/Django/Projects/Ivhrms
    source ivenv/Scripts/activate
    python /path/to/staff_attendance/install.py        (or give the project folder as the first argument)

What it does, in order:
  1. copies the new files (a file that already exists and differs is saved as <name>.bak first)
  2. copies the two Settings descriptions (settings_spec.py) only if the app has none yet
  3. makes the small edits to existing files; each says ok, already, or MANUAL with exact instructions
  4. creates the migration for the weekly-off rule (skip with --no-migrations)
Safe to run twice. Windows line endings are kept. Git Bash paths such as /c/Django/... are understood."""
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
FILES, IF_ABSENT = HERE / "files", HERE / "files_if_absent"
manual_items = []


def fix_path(raw):
    m = re.match(r"^/([a-zA-Z])/(.*)$", raw)
    return f"{m.group(1).upper()}:/{m.group(2)}" if m and os.name == "nt" else raw


def read(path):
    raw = path.read_bytes().decode("utf-8")
    return raw.replace("\r\n", "\n"), "\r\n" in raw


def write(path, text, crlf):
    path.write_bytes((text.replace("\n", "\r\n") if crlf else text).encode("utf-8"))


def manual(label, rel, how):
    manual_items.append(label)
    print(f"  MANUAL  {label}\n          file: {rel}\n" + "\n".join("          " + line for line in how.splitlines()))


def copy_files(root):
    for src in sorted(FILES.rglob("*")):
        if not src.is_file() or src.suffix == ".pyc" or "__pycache__" in src.parts:
            continue
        rel = src.relative_to(FILES)
        dest = root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            if dest.read_bytes() == src.read_bytes():
                print(f"  already {rel.as_posix()}")
                continue
            shutil.copy2(dest, dest.with_name(dest.name + ".bak"))
            print(f"  ok      {rel.as_posix()}   (the old one is saved as {dest.name}.bak)")
        else:
            print(f"  ok      {rel.as_posix()}   (new)")
        shutil.copy2(src, dest)


def copy_if_absent(root):
    for src in sorted(IF_ABSENT.rglob("*")):
        if not src.is_file() or src.suffix == ".pyc" or "__pycache__" in src.parts:
            continue
        rel = src.relative_to(IF_ABSENT)
        dest = root / rel
        if not dest.parent.is_dir():
            manual(f"{rel.as_posix()}", rel.as_posix(), f"The app folder {dest.parent} was not found.")
            continue
        if dest.exists():
            if dest.read_bytes() == src.read_bytes():
                print(f"  already {rel.as_posix()}")
            else:
                manual(f"{rel.as_posix()}: it exists already and was left alone", rel.as_posix(),
                       f"Add the Module registered in {src} to it by hand (or replace the file, keeping its other modules).")
            continue
        shutil.copy2(src, dest)
        print(f"  ok      {rel.as_posix()}   (new)")


def edit(root, rel, label, old, new, marker):
    path = root / rel
    if not path.exists():
        return manual(label, rel, f"The file was not found. Make this change by hand:\n  find:\n{old}\n  replace with:\n{new}")
    text, crlf = read(path)
    if marker in text:
        return print(f"  already {label}")
    if text.count(old) != 1:
        why = "the text to change was not found" if old not in text else "the text to change appears more than once"
        return manual(label, rel, f"({why}) Make this change by hand:\n  find:\n{old}\n  replace with:\n{new}")
    write(path, text.replace(old, new), crlf)
    print(f"  ok      {label}")


def append(root, rel, label, line, marker):
    path = root / rel
    if not path.exists():
        return manual(label, rel, f"The file was not found. Add this at the very end:\n{line}")
    text, crlf = read(path)
    if marker in text:
        return print(f"  already {label}")
    write(path, text.rstrip("\n") + "\n\n" + line + "\n", crlf)
    print(f"  ok      {label}")


TAB = ("{% if perms.attendance.add_attendance %}<li class=\"nav-item\"><a class=\"nav-link{% if tab == 'attendance' %} "
       "active{% endif %}\" href=\"{% url 'web:staff_attendance' %}\">Attendance</a></li>{% endif %}")
BUTTON = ("{% if perms.attendance.add_attendance and employee.status != 'separated' and employee.worker_type != 'labor' %}"
          "<a class=\"btn btn-outline-primary\" href=\"{% url 'web:staff_attendance' %}?employee={{ employee.pk }}\">"
          "Mark attendance</a>{% endif %}")


def add_tab(root):
    rel, label = "apps/web/templates/web/staff/_tabs.html", "staff/_tabs.html: the Attendance tab"
    path = root / rel
    if not path.exists():
        return manual(label, rel, f"Add this after the last tab, inside the <ul>:\n{TAB}")
    text, crlf = read(path)
    if "web:staff_attendance" in text:
        return print(f"  already {label}")
    m = re.search(r"Family &amp; Visas</a>\s*</li>(\s*{%\s*endif\s*%})?", text)
    if not m:
        return manual(label, rel, f"The Family & Visas tab was not found. Add this after the last tab, inside the <ul>:\n{TAB}")
    write(path, text[:m.end()] + "\n  " + TAB + text[m.end():], crlf)
    print(f"  ok      {label}")


def add_button(root):
    rel, label = "apps/web/templates/web/staff/detail.html", "staff/detail.html: the Mark attendance button"
    old = """<a class="btn btn-outline-primary" href="{% url 'web:employee_edit' employee.pk %}">Edit details</a>"""
    path = root / rel
    if not path.exists():
        return manual(label, rel, f"Add this next to the Edit details button:\n{BUTTON}")
    text, crlf = read(path)
    if "web:staff_attendance" in text:
        return print(f"  already {label}")
    if text.count(old) != 1:
        return manual(label, rel, f"The Edit details button was not found. Add this next to it:\n{BUTTON}")
    write(path, text.replace(old, old + "\n      " + BUTTON), crlf)
    print(f"  ok      {label}")


ROUTES = (
    '    path("settings/holidays/", scheduling_settings.holiday_list, name="holiday_list"),\n'
    '    path("settings/holidays/new/", scheduling_settings.holiday_add, name="holiday_add"),\n'
    '    path("settings/holidays/<int:pk>/", scheduling_settings.holiday_edit, name="holiday_edit"),\n'
    '    path("settings/holidays/<int:pk>/toggle/", scheduling_settings.holiday_toggle, name="holiday_toggle"),\n'
    '    path("settings/weekly-off/", scheduling_settings.weekly_off, name="weekly_off"),\n')
CATCH_ALL = '    path("settings/<slug:module>/", settings_hub.settings_module, name="settings_module"),'


def settings_routes(root):
    rel, label = "apps/web/urls/settings_hub.py", "urls/settings_hub.py: the holiday and weekly-off routes"
    path = root / rel
    if not path.exists():
        return manual(label, rel, "The file was not found.")
    text, crlf = read(path)
    if "scheduling_settings.holiday_list" in text:
        return print(f"  already {label}")
    if text.count(CATCH_ALL) != 1:
        return manual(label, rel, "The catch-all route settings/<slug:module>/ was not found. Add these lines ABOVE it, "
                                  "and `scheduling_settings` to the `from apps.web.views import ...` line:\n" + ROUTES)
    text = text.replace(CATCH_ALL, ROUTES + CATCH_ALL)
    m = re.search(r"^(from apps\.web\.views import )(.*\bsettings_hub\b.*)$", text, re.M)
    if not m:
        return manual(label, rel, "Add `from apps.web.views import scheduling_settings` at the top, and these routes "
                                  "ABOVE the catch-all settings/<slug:module>/ line:\n" + ROUTES)
    if "scheduling_settings" not in m.group(2):
        text = text[:m.start()] + m.group(1) + m.group(2).rstrip() + ", scheduling_settings" + text[m.end():]
    write(path, text, crlf)
    print(f"  ok      {label}")


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    root = Path(fix_path(args[0])) if args else Path.cwd()
    if not (root / "manage.py").exists() or not (root / "apps" / "scheduling").is_dir():
        sys.exit(f"{root} does not look like the Ivhrms project (manage.py and apps/scheduling are expected). "
                 "Run this from the project folder, or pass the folder as the first argument.")
    views = root / "apps" / "web" / "views" / "attendance.py"
    if not views.exists() or "attendance_day" not in views.read_text(encoding="utf-8"):
        sys.exit("The attendance calendar is not installed yet (apps/web/views/attendance.py has no attendance_day). "
                 "Install it first: the staff route sits beside its routes.")
    print(f"Project: {root}\n\n1. Files")
    copy_files(root)
    print("\n2. Settings descriptions (only where the app has none)")
    copy_if_absent(root)

    print("\n3. Changes to existing files")
    append(root, "apps/scheduling/models.py", "scheduling/models.py: load the weekly-off rule",
           "from .weekly_off import WeeklyOffRule  # noqa: E402,F401  (the company's weekly off)", "from .weekly_off import")
    edit(root, "apps/scheduling/services.py", "scheduling/services.py: import",
         "from .models import Holiday, ShiftAssignment, default_weekly_off\n",
         "from .models import Holiday, ShiftAssignment, default_weekly_off\n"
         "from .calendar_rules import company_weekly_off, weekly_off_on  # noqa: F401  (weekly_off_on is used elsewhere)\n",
         "from .calendar_rules import")
    edit(root, "apps/scheduling/services.py", "scheduling/services.py: the company's weekly off, by date",
         "    default_off = default_weekly_off()\n    result, d = {}, start\n",
         "    company_off = company_weekly_off(employee.company_id, start, end)         # {date: weekday numbers}\n"
         "    result, d = {}, start\n",
         "company_off = company_weekly_off(")
    edit(root, "apps/scheduling/services.py", "scheduling/services.py: use it for people with no shift",
         "            off = default_off\n", "            off = company_off[d]\n", "off = company_off[d]")
    edit(root, "apps/web/calendar_hints.py", "calendar_hints.py: the leave calendar follows the company's weekly off",
         "    python_days = shift.weekly_off_days if shift else scheduling.default_weekly_off()\n",
         "    python_days = shift.weekly_off_days if shift else scheduling.weekly_off_on(employee.company_id, today)\n",
         "scheduling.weekly_off_on(")
    settings_routes(root)
    add_tab(root)
    add_button(root)

    print("\n4. Migration")
    if "--no-migrations" in sys.argv:
        print("  skipped (--no-migrations). Run: python manage.py makemigrations scheduling -n weekly_off_rule")
    else:
        run = subprocess.run([sys.executable, "manage.py", "makemigrations", "scheduling", "-n", "weekly_off_rule"],
                             cwd=root, capture_output=True, text=True)
        print("  " + (run.stdout.strip() or run.stderr.strip()).replace("\n", "\n  "))
        if run.returncode != 0:
            manual_items.append("makemigrations")
            print("  MANUAL  makemigrations did not finish. Activate the virtualenv (ivenv) and run:\n"
                  "          python manage.py makemigrations scheduling -n weekly_off_rule")
    if manual_items:
        print(f"\n{len(manual_items)} item(s) need you. Do the MANUAL steps above, then continue with NOTES.md.")
        sys.exit(1)
    print("\nDone. Next: python manage.py migrate   then   python manage.py test apps.scheduling apps.attendance apps.web   (see NOTES.md)")


if __name__ == "__main__":
    main()
