#!/usr/bin/env python
"""Install Payroll phase 2 (payroll policy, nationality check, staff payroll runs) into Ivhrms.

It works on a project that already has phase 1, and also on one that has nothing yet: the `files` folder holds the
whole payroll module.

Run it from the project folder, the one that holds manage.py:

    python /path/to/payroll_phase2/install_payroll_phase2.py

What it does
  1. Copies the files from the `files` folder next to this script into the project.
       - A file that is not there yet is created.
       - A file that is already there and identical is skipped.
       - A file that is exactly an earlier version I gave you (phase 1, or the first phase 2 package) is UPDATED.
       - Any other file that differs (you changed it) is NEVER overwritten: the new version is saved beside it with
         a `.payroll2` ending, and you are told.
  2. Makes four small edits to existing files (settings, urls, sidebar, staff page). Phase 1 already made them, so on
     an upgrade they are reported as already done. If a place to edit cannot be found, the script prints the exact
     lines to add by hand.

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
    "apps/payroll/__init__.py": ["e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"],
    "apps/payroll/apps.py": ["c30219dc23d3ae6fc0478cdba42b344ccf410cf90733cb4c90ba14418a7c07f0", "2f45f69e378a994c388e1edc4204e2167bfa0da80b9320774895770d81cc4a3d"],
    "apps/payroll/calculation.py": ["93cddc15aae6faf3ee8a331f40739fa4ad135a990da52f8e3f00f5277e5119c4"],
    "apps/payroll/countries.py": ["8d8342a1ced416758cbd40309505539918cfb77981b82991aa537402bbc5099d"],
    "apps/payroll/defaults.py": ["1cbc57ec0e11abced3a0eaf674d520d92fcd4f6fbc387556a2e40312ce4b7ecf"],
    "apps/payroll/migrations/0001_initial.py": ["ddf42f91f925ab5f976516da2a0c1002f55a20e89b3559d6ab7483173fb4b614"],
    "apps/payroll/migrations/0002_payroll_runs.py": ["c080c3945834de60af70f117b63c00978f2a3d3e83549833f3d886fe866cdb1b"],
    "apps/payroll/migrations/__init__.py": ["e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"],
    "apps/payroll/models.py": ["44e446fe41cce18fd5a958175fd28fcd273d3f6dc7e51d85bb22819ef28e427e", "bae8eea6d2b03e4d78fe41b8eb53dbd5967d6c355f564652fa0a423b0fbbe064"],
    "apps/payroll/nationality.py": ["c07dff9e60b631d3b896197bfd00e222822ec7cabd1d05153caa62a074492abf"],
    "apps/payroll/outputs.py": ["c8349c9aaf2b9f6852e291f004464f5ac23c7467ca9e424391c1b607f0dafef2"],
    "apps/payroll/payrun.py": ["f47edbcbda72a9f4f57a7c6594ea4d10a857e83e655054c509ad2c67c5a6afed"],
    "apps/payroll/policy.py": ["14efd583c7e6ffeb513fafc0945491c66ed32f09719a19b5561122bf1d4b2965"],
    "apps/payroll/runs.py": ["4d84f07936d7318a74887ede67701ff9ff843d060532e2a5a4944fdf5be6cdd5"],
    "apps/payroll/services.py": ["da1a34d719657a2c199bfb351a35b4eb6bd5b78cb18de119171a02173b325132", "a1e342cff35dceafd1e74fa24b7f27888f467047e0a3856a8ce57b6bceeb123a"],
    "apps/payroll/settings_spec.py": ["5980f9770af53f4f0520bfa25652439fb1d2d5c3a93de4d8548e7e95f72b5dee", "fc14de48b4eb290de878751949a8ee25e6e0541fd460ed27295ba2ae72fa241d"],
    "apps/payroll/signals.py": ["53a9a697941a3202db1a7e2cb092dbffd911bee64ec97435ad03c132cf6a657e"],
    "apps/payroll/test_calculation.py": ["6c929195ac8068f2fa47d771f283639862e317d4c0daba48852a3100b8f657e0"],
    "apps/payroll/test_runs.py": ["ae6275b7be53367f5246c1f5213d7b8041506c61a856fb57df1751cd50d60df0"],
    "apps/payroll/tests.py": ["d38d1b6b7859b9231b4ed553e016105bfe75877943450b346a5c1508c9ed4497", "2871cd4b7fdc9412eb6216a8873edc70d5954020d38571a58cee1cddcfd5bb83"],
    "apps/web/forms/payroll.py": ["25a41a8dd6a3e0c9d5818bd939c740dc6e448386d11139053cdc62e307855043"],
    "apps/web/forms/payroll_runs.py": ["523295a6325b7bc59332c15bd40548ec6d804c974d2dcf17dc815df6e60a04b2"],
    "apps/web/templates/web/payroll/_staff_card.html": ["0267514e84a31a49ed3a03dc28f81831759ccd7231239f66ee5ac4238e7f8bd2"],
    "apps/web/templates/web/payroll/_tabs.html": ["06feb68b33eb7ec52a189a406873947ca09bbe4df4971cdf2189bfc8ac1fbd5d", "b7f49beca7093bec12bf9079a189746ad2bf67c88882175fe5f27eb08d36e8af"],
    "apps/web/templates/web/payroll/allowances.html": ["1efaee2a63a612736e6a1e300330eabfc4ef0cc92195b34d424fd14cf18c2cd3"],
    "apps/web/templates/web/payroll/employee.html": ["c8f58a1b4a189b2ceaa7c32244a1cc09424be6240ef6308f8018dd514564c6ec"],
    "apps/web/templates/web/payroll/home.html": ["8d0dd58df55f7c16fb33a828a0d228bc85daea2b6d8ef4e231b4195f1266baaf", "1a57943385797b71caa2cfe1c371e9c3d5157515d967078767ea0bb5cd8b770b"],
    "apps/web/templates/web/payroll/line.html": ["bdbe2192cb3df064c8d6ddaf0a96a728a5c7810fa0bfd2362389dcff094f7684"],
    "apps/web/templates/web/payroll/policy.html": ["03ceee327b50adca4ae1e5a518b81f8f43948750670a5fef9abafb92b0f87f7f"],
    "apps/web/templates/web/payroll/run.html": ["737e2353bcdd2ef80f1f5bc2be731e5fa04072bc02818bbda4817ce13de60133"],
    "apps/web/templates/web/payroll/runs.html": ["8c37b20375f24a34d5db6aed29f7d75045afaa707594622206c7f8eb0e06e6df"],
    "apps/web/templates/web/settings/_payroll_allowances.html": ["ad25c53fef7e5f098503b0e140650e32ef0b595d99f56eec16f08e912afa8a8e"],
    "apps/web/templates/web/settings/_payroll_policy.html": ["32c0ae178fc5218c83067f6d474ebc8db38a9335ce19471f01e90d631eba1cf3"],
    "apps/web/templatetags/payroll_tags.py": ["c90957d425856b5109bd1357b3afae7f339057b7c2532db5cdad3e767fe99331"],
    "apps/web/tests/test_payroll.py": ["e9fa6b6c17563f78c5dff5cc4f8ec6a8e51bb90de40f76f54ef720a439f5cd44", "3835db8c29a8b2561526648c29c5b57ca8e2f04d6c989a0bc0e7b8bfd6881328"],
    "apps/web/tests/test_payroll_runs.py": ["8d142e320eac015a9f4ae0b3b71f4b03a4dc9bd3dce34ee48ad873cc8de71638"],
    "apps/web/urls/payroll.py": ["b2f12de35ffad9b8db7a7b9d092a738ab5b04bdd1254f738d7aafa854f8c96e2", "af72fe820f13b1dfb9f9f977c79aa8fcd2ccfc34b74494731c5fbb234006a896"],
    "apps/web/views/payroll.py": ["61da4da5dc2d69695ec1b88b46881cf74107cab889a8e6db9f98bb0f19ed0460", "43bdc064c1ae34e76080bf8fe9481752d6fca7b4aac5f15ad9c09247f1b70f63"],
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
            side = target.with_name(target.name + ".payroll2")
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

    print("\nPayroll phase 2 installer\n" + "=" * 28)
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
    print("\nNext: create the tables and run the tests, as in the notes (README_payroll_phase2.md).")


if __name__ == "__main__":
    main()
