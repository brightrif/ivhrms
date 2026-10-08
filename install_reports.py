#!/usr/bin/env python3
"""Install Labor Management, the Reports tab (deployment, manpower, cost and contractor statement, each with Excel export), on top of the Settings page.

Run from the project root (the folder with manage.py), on a new git branch:

    python /path/to/install_reports.py

This adds new files and updates three earlier ones (the tab bar, the labor URLs and the list of rights offered on the Access tab). An earlier file is replaced
only if it is still exactly as the earlier phase delivered it (line endings ignored). If you changed it, it is left alone and
the new version is saved next to it with the ending .reports, so you can merge by hand. There is no database
migration in this phase. Running the installer twice changes nothing."""
import hashlib
import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
FILES = HERE / "files"
PREVIOUS = json.loads((HERE / "previous_hashes.json").read_text())
ROOT = Path.cwd()
changed, problems = [], []


def digest(data):
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


def install_file(src):
    rel = src.relative_to(FILES).as_posix()
    dest = ROOT / rel
    new = src.read_bytes()
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
        changed.append(f"added    {rel}")
        return
    current = dest.read_bytes()
    if digest(current) == digest(new):
        return
    if rel in PREVIOUS:
        if digest(current) == PREVIOUS[rel]:
            eol = b"\r\n" if b"\r\n" in current else b"\n"
            dest.write_bytes(new.replace(b"\r\n", b"\n").replace(b"\n", eol))
            changed.append(f"updated  {rel}")
        else:
            side = dest.with_name(dest.name + ".reports")
            shutil.copyfile(src, side)
            problems.append(f"{rel} has been changed since it was delivered, so it was not replaced. The new version is in "
                            f"{side.relative_to(ROOT).as_posix()}; merge the two by hand and delete the .reports file.")
    else:
        problems.append(f"{rel} already exists and is different. Not touched; compare it by hand.")


def main():
    if not (ROOT / "manage.py").exists() or not (ROOT / "apps").is_dir():
        sys.exit("Run this from the Ivhrms project root (the folder that contains manage.py and apps/).")
    if not (ROOT / "apps/labor/settings_spec.py").exists():
        sys.exit("The Settings page is not installed here. Install Labor phases 1 to 5, the overtime-decision update and the Settings page first.")
    if not (ROOT / "apps/attendance/services.py").exists():
        sys.exit("The attendance app was not found (apps/attendance/services.py). Phase 3 records attendance through it.")
    for src in sorted(FILES.rglob("*")):
        if src.is_file() and "__pycache__" not in src.parts:
            install_file(src)
    print("\n".join(changed) or "Nothing to change: the Reports tab is already installed.")
    if problems:
        print("\nNeeds your attention:")
        print("\n".join(f"  - {p}" for p in problems))
        sys.exit(1)
    print("\nNo migration needed. Next: python manage.py test apps.labor apps.web")


if __name__ == "__main__":
    main()
