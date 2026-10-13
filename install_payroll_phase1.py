#!/usr/bin/env python
"""Install Payroll phase 1 (staff salary structure, allowance types, payment details) into Ivhrms.

Run it from the project folder, the one that holds manage.py:

    python /path/to/payroll_phase1/install_payroll_phase1.py

What it does
  1. Copies the new files from the `files` folder next to this script into the project.
     A file that is already there and identical is skipped. A file that is already there and different is NEVER
     overwritten: the new version is saved beside it with a `.payroll1` ending, and you are told.
  2. Makes four small edits to existing files (settings, urls, sidebar, staff page). Each one is skipped if it is
     already done, so running the script twice is safe. If a place to edit cannot be found, the script prints the
     exact lines to add by hand.

It keeps each file's own line endings (Windows or Unix) and does not touch the database.
"""
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
FILES = HERE / "files"
ROOT = Path.cwd()

copied, skipped, aside, edited, already, manual = [], [], [], [], [], []


def die(message):
    print(f"\nSTOP: {message}")
    sys.exit(1)


def project_uses_crlf():
    manage = ROOT / "manage.py"
    return b"\r\n" in manage.read_bytes()


def to_style(text, crlf):
    text = text.replace("\r\n", "\n")
    return text.replace("\n", "\r\n") if crlf else text


# ------------------------------------------------------------------ step 1: copy new files

def copy_new_files():
    crlf = project_uses_crlf()
    for source in sorted(p for p in FILES.rglob("*") if p.is_file() and "__pycache__" not in p.parts):
        relative = source.relative_to(FILES)
        target = ROOT / relative
        text = source.read_text(encoding="utf-8")
        wanted = to_style(text, crlf).encode("utf-8")
        if target.exists():
            if target.read_bytes().replace(b"\r\n", b"\n") == wanted.replace(b"\r\n", b"\n"):
                skipped.append(str(relative))
                continue
            side = target.with_name(target.name + ".payroll1")
            side.write_bytes(wanted)
            aside.append(f"{relative}  ->  saved as {side.name}")
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(wanted)
        copied.append(str(relative))


# ------------------------------------------------------------------ step 2: small edits to existing files

def read_text(path):
    raw = path.read_bytes()
    crlf = b"\r\n" in raw
    return raw.decode("utf-8").replace("\r\n", "\n"), crlf


def write_text(path, text, crlf):
    path.write_bytes((text.replace("\n", "\r\n") if crlf else text).encode("utf-8"))


def edit(relative, label, already_done, apply, manual_text):
    path = ROOT / relative
    if not path.exists():
        manual.append((label, relative, manual_text))
        return
    text, crlf = read_text(path)
    if already_done(text):
        already.append(label)
        return
    new = apply(text)
    if new is None:
        manual.append((label, relative, manual_text))
        return
    write_text(path, new, crlf)
    edited.append(label)


def insert_before_line(text, pattern, new_lines):
    """Put new_lines (already indented as wanted, may contain {indent}) before the first line matching pattern."""
    match = re.search(pattern, text, flags=re.M)
    if not match:
        return None
    indent = match.group(1) if match.groups() else ""
    block = new_lines.replace("{indent}", indent)
    return text[:match.start()] + block + text[match.start():]


def edit_settings():
    def apply(text):
        for pattern in (r'^(\s*)["\']apps\.configuration["\'],', r'^(\s*)["\']apps\.web["\'],'):
            out = insert_before_line(text, pattern, '{indent}"apps.payroll",\n')
            if out:
                return out
        return None

    edit("config/settings/base.py", "Settings: add apps.payroll to LOCAL_APPS",
         lambda t: re.search(r'["\']apps\.payroll["\']', t) is not None, apply,
         'In LOCAL_APPS, before "apps.configuration", add the line:\n    "apps.payroll",')


def edit_urls():
    def apply(text):
        out = insert_before_line(text, r'^()app_name\s*=', "from . import payroll  # noqa: E402\n\n")
        if out is None:
            return None
        patterns = r'^(\s*)\*settings_hub\.urlpatterns,\s*$'
        done = insert_before_line(out, patterns, "{indent}*payroll.urlpatterns,\n")
        if done is None:
            done = out.rstrip("\n") + "\n\nurlpatterns += payroll.urlpatterns\n"
        return done

    edit("apps/web/urls/__init__.py", "URLs: include the payroll pages",
         lambda t: "payroll" in t, apply,
         'Add  "from . import payroll"  with the other imports, and  "*payroll.urlpatterns,"  in the urlpatterns list.')


def edit_sidebar():
    link = ('{indent}{% if perms.payroll.view_salaryrecord %}\n'
            '{indent}<a class="nav-link {% if u|slice:\':8\' == \'payroll_\' %}active{% endif %}" '
            'href="{% url \'web:payroll_home\' %}">\n'
            '{indent}  <i class="bi bi-cash-coin"></i> Payroll</a>\n'
            '{indent}{% endif %}\n')
    edit("apps/web/templates/web/base.html", "Sidebar: add the Payroll link",
         lambda t: "web:payroll_home" in t,
         lambda t: insert_before_line(t, r'^(\s*)\{%\s*if perms\.configuration\s*%\}', link),
         "In the sidebar of base.html, just before  {% if perms.configuration %}  add:\n"
         + link.replace("{indent}", "          "))


def edit_staff_page():
    line = '<div class="mb-4">{% load payroll_tags %}{% employee_pay employee %}</div>\n'

    def apply(text):
        match = re.search(r'^.*\{%\s*employee_documents\s+employee\s*%\}.*\n', text, flags=re.M)
        if not match:
            return None
        return text[:match.end()] + line + text[match.end():]

    edit("apps/web/templates/web/staff/detail.html", "Staff page: show the Pay card",
         lambda t: "employee_pay" in t, apply,
         "In staff/detail.html, under the documents card, add this line:\n    " + line.strip())


# ------------------------------------------------------------------ run

def main():
    if not (ROOT / "manage.py").exists() or not (ROOT / "apps" / "web").is_dir():
        die("Run this from the Ivhrms project folder (the one with manage.py and apps/). "
            f"You are in: {ROOT}")
    if not FILES.is_dir():
        die(f"The 'files' folder was not found next to this script: {FILES}")

    copy_new_files()
    edit_settings()
    edit_urls()
    edit_sidebar()
    edit_staff_page()

    print("\nPayroll phase 1 installer\n" + "=" * 28)
    print(f"New files copied:       {len(copied)}")
    for name in copied:
        print(f"   + {name}")
    print(f"Already in place:       {len(skipped)} file(s)")
    if aside:
        print("Files that differ from the new version (yours was kept):")
        for note in aside:
            print(f"   ! {note}")
    print(f"Existing files edited:  {len(edited)}")
    for label in edited:
        print(f"   ~ {label}")
    if already:
        print("Edits already done:")
        for label in already:
            print(f"   = {label}")
    if manual:
        print("\nDO THESE BY HAND (the place to edit was not found):")
        for label, relative, text in manual:
            print(f"\n * {label}\n   File: {relative}\n   {text}")
    print("\nNext: create the tables and run the tests, as in the notes (README_payroll_phase1.md).")


if __name__ == "__main__":
    main()
