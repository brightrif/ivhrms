#!/usr/bin/env python
"""Install the direct labor payroll run into Ivhrms (on top of payroll phases 1 and 2).

It works on a project that already has payroll phases 1 and 2, and also on one that has nothing yet: the `files`
folder holds the whole payroll module. It also adds a few small lock checks to the labor module (see below).

Run it from the project folder, the one that holds manage.py:

    python /path/to/payroll_labor/install_payroll_labor.py

What it does
  1. Copies the files from the `files` folder next to this script into the project.
       - A file that is not there yet is created.
       - A file that is already there and identical is skipped.
       - A file that is exactly an earlier version I gave you (phase 1, or the first phase 2 package) is UPDATED.
       - Any other file that differs (you changed it) is NEVER overwritten: the new version is saved beside it with
         a `.payroll3` ending, and you are told.
  2. Makes four small edits to existing files (settings, urls, sidebar, staff page; already done on an upgrade),
     and seven small lock checks in the labor module, so that once a labor payroll run is approved its month cannot
     quietly change: new pay terms, hours and overtime for that month are refused. Every edit is skipped if it is
     already done. If a place to edit cannot be found, the script prints the exact lines to add by hand.

It keeps each file's own line endings (Windows or Unix) and does not touch the database.
"""
import hashlib
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
FILES = HERE / "files"
ROOT = Path.cwd()

# the SHA-256 of every earlier version of each file I delivered (line endings ignored): safe to update
OLD_VERSIONS = {
    "apps/payroll/apps.py": ["2f45f69e378a994c388e1edc4204e2167bfa0da80b9320774895770d81cc4a3d", "c30219dc23d3ae6fc0478cdba42b344ccf410cf90733cb4c90ba14418a7c07f0"],
    "apps/payroll/models.py": ["44e446fe41cce18fd5a958175fd28fcd273d3f6dc7e51d85bb22819ef28e427e"],
    "apps/payroll/payrun.py": ["f47edbcbda72a9f4f57a7c6594ea4d10a857e83e655054c509ad2c67c5a6afed"],
    "apps/payroll/runs.py": ["4d84f07936d7318a74887ede67701ff9ff843d060532e2a5a4944fdf5be6cdd5"],
    "apps/payroll/services.py": ["da1a34d719657a2c199bfb351a35b4eb6bd5b78cb18de119171a02173b325132"],
    "apps/payroll/settings_spec.py": ["5980f9770af53f4f0520bfa25652439fb1d2d5c3a93de4d8548e7e95f72b5dee"],
    "apps/payroll/test_labor_runs.py": ["783c944f8e127f50ac46b0548234adcd5426b681644fe0e20c20328f992e5f58"],
    "apps/payroll/test_runs.py": ["45dab84d6b97745ebbab4e1f5cea05f3dc7b40d989887570d542323bf0cb52a6", "ae6275b7be53367f5246c1f5213d7b8041506c61a856fb57df1751cd50d60df0"],
    "apps/payroll/tests.py": ["2871cd4b7fdc9412eb6216a8873edc70d5954020d38571a58cee1cddcfd5bb83", "d38d1b6b7859b9231b4ed553e016105bfe75877943450b346a5c1508c9ed4497"],
    "apps/web/forms/payroll_runs.py": ["523295a6325b7bc59332c15bd40548ec6d804c974d2dcf17dc815df6e60a04b2"],
    "apps/web/templates/web/payroll/_tabs.html": ["06feb68b33eb7ec52a189a406873947ca09bbe4df4971cdf2189bfc8ac1fbd5d", "b7f49beca7093bec12bf9079a189746ad2bf67c88882175fe5f27eb08d36e8af"],
    "apps/web/templates/web/payroll/home.html": ["8d0dd58df55f7c16fb33a828a0d228bc85daea2b6d8ef4e231b4195f1266baaf"],
    "apps/web/templates/web/payroll/line.html": ["bdbe2192cb3df064c8d6ddaf0a96a728a5c7810fa0bfd2362389dcff094f7684"],
    "apps/web/templates/web/payroll/run.html": ["737e2353bcdd2ef80f1f5bc2be731e5fa04072bc02818bbda4817ce13de60133"],
    "apps/web/templates/web/payroll/runs.html": ["8c37b20375f24a34d5db6aed29f7d75045afaa707594622206c7f8eb0e06e6df"],
    "apps/web/tests/test_payroll.py": ["e9fa6b6c17563f78c5dff5cc4f8ec6a8e51bb90de40f76f54ef720a439f5cd44"],
    "apps/web/tests/test_payroll_runs.py": ["662a4d8408885a5eea3cbad4f93eb5ab5c7ef1ebb93cf72dac53816690d937f9", "8d142e320eac015a9f4ae0b3b71f4b03a4dc9bd3dce34ee48ad873cc8de71638"],
    "apps/web/urls/payroll.py": ["b2f12de35ffad9b8db7a7b9d092a738ab5b04bdd1254f738d7aafa854f8c96e2"],
    "apps/web/views/payroll.py": ["61da4da5dc2d69695ec1b88b46881cf74107cab889a8e6db9f98bb0f19ed0460"],
    "apps/web/views/payroll_runs.py": ["eb0c2d22ca174fa3f5b0f47504a1dec7bf81dda76f4d00c2443bab81ac34432f"],
}

copied, updated, skipped, aside, edited, already, manual = [], [], [], [], [], [], []


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
            on_disk = target.read_bytes().replace(b"\r\n", b"\n")
            if on_disk == wanted.replace(b"\r\n", b"\n"):
                skipped.append(str(relative))
                continue
            if hashlib.sha256(on_disk).hexdigest() in OLD_VERSIONS.get(relative.as_posix(), []):
                target.write_bytes(wanted)                       # still exactly as I gave it before: safe to update
                updated.append(str(relative))
                continue
            side = target.with_name(target.name + ".payroll3")
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
            '{indent}{% elif perms.payroll.view_payrollrun %}\n'
            '{indent}<a class="nav-link {% if u|slice:\':8\' == \'payroll_\' %}active{% endif %}" '
            'href="{% url \'web:payroll_runs\' %}">\n'
            '{indent}  <i class="bi bi-cash-coin"></i> Payroll</a>\n'
            '{indent}{% endif %}\n')

    def apply(text):
        # phase 1 added a link for people who may see salaries only: replace it, so run-only users get one too
        old = re.search(r'^([ \t]*)\{% if perms\.payroll\.view_salaryrecord %\}.*?\{% endif %\}\n', text,
                        flags=re.S | re.M)
        if old:
            return text[:old.start()] + link.replace("{indent}", old.group(1)) + text[old.end():]
        return insert_before_line(text, r'^(\s*)\{%\s*if perms\.configuration\s*%\}', link)

    edit("apps/web/templates/web/base.html", "Sidebar: the Payroll link",
         lambda t: "web:payroll_runs" in t, apply,
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


# ------------------------------------------------------------------ labor lock checks (small edits to labor files)

HOOK = "from . import locks  # payroll lock: "
# (file, name, text the new lines go next to, "before" or "after", the new lines, with {i} for the indent)
LABOR_HOOKS = [
    ("apps/labor/services.py", "change_rate",
     "    _check_terms(wage_basis=wage_basis, rate=rate, standard_hours=standard_hours, engagement=profile.engagement)\n",
     "after", ["from . import locks  # payroll lock: change_rate",
               'locks.ensure_open([profile.employee_id], effective_from, None, "starting new pay terms then")']),
    ("apps/labor/timekeeping.py", "save_hours",
     "    sheet = build_timesheet(user, project, location, first, last)\n    done, skipped = Counter(), []\n",
     "after", ["from . import locks  # payroll lock: save_hours",
               'locks.ensure_open([row.employee.pk for row in sheet.rows], first, last, "changing hours")']),
    ("apps/labor/timekeeping.py", "reopen_period",
     '    if not entries:\n        raise LaborError("Nothing is confirmed in this period.")\n',
     "after", ["from . import locks  # payroll lock: reopen_period",
               'locks.ensure_open([e.employee_id for e in entries], first, last, "reopening hours")']),
    ("apps/labor/hours_split.py", "split_days",
     "    cells_by_project = {}\n",
     "before", ["from . import locks  # payroll lock: split_days",
                'locks.ensure_open([employee.pk], first, last, "dividing hours")']),
    ("apps/labor/overtime_services.py", "prepare_claims",
     "    kinds, rates, created, skipped = {}, {}, 0, []\n",
     "before", ["from . import locks  # payroll lock: prepare_claims",
                'locks.ensure_open({e.employee_id for e in entries}, first, last, "preparing overtime claims")']),
    ("apps/labor/overtime_services.py", "decide",
     "        claim.status = Claim.Status.APPROVED if approve else Claim.Status.REJECTED\n",
     "before", ["        from . import locks  # payroll lock: decide",
                '        locks.ensure_open([claim.employee_id], claim.date, claim.date, "deciding overtime")']),
    ("apps/labor/overtime_services.py", "void_claims",
     '        raise LaborError("There are no overtime claims in this period.")\n',
     "after", ["from . import locks  # payroll lock: void_claims",
               'locks.ensure_open({c.employee_id for c in claims}, first, last, "voiding overtime claims")']),
]


def edit_labor_hooks():
    for relative, name, anchor, where, lines in LABOR_HOOKS:
        label = f"Labor: lock check in {name}"
        path = ROOT / relative
        manual_text = (f"In {relative}, in {name}(), {where} this line:\n    {anchor.strip().splitlines()[-1].strip()}\n"
                       "add:\n" + "\n".join("    " + l for l in lines))
        if not path.exists():
            manual.append((label, relative, manual_text))
            continue
        text, crlf = read_text(path)
        if f"# payroll lock: {name}" in text:
            already.append(label)
            continue
        if text.count(anchor) != 1:                      # not found, or not unique: never guess where to put it
            manual.append((label, relative, manual_text))
            continue
        indent = "    " if not lines[0].startswith(" ") else ""
        block = "".join(f"{indent}{l}\n" for l in lines)
        at = text.index(anchor)
        at = at + len(anchor) if where == "after" else at
        write_text(path, text[:at] + block + text[at:], crlf)
        edited.append(label)


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
    edit_labor_hooks()

    print("\nPayroll labor installer\n" + "=" * 26)
    print(f"New files copied:       {len(copied)}")
    for name in copied:
        print(f"   + {name}")
    print(f"Earlier files updated:  {len(updated)}")
    for name in updated:
        print(f"   ^ {name}")
    print(f"Already in place:       {len(skipped)} file(s)")
    if aside:
        print("Files you have changed, so yours was kept and the new version saved beside it:")
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
    print("\nNext: create the tables and run the tests, as in the notes (README_payroll_labor.md).")


if __name__ == "__main__":
    main()
