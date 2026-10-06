#!/usr/bin/env python3
"""Reorganise apps/web into packages: views/, forms/, tests/, urls/ and per-feature template folders.

    python tools/restructure_web.py              dry run: shows the plan and any problem, changes nothing
    python tools/restructure_web.py --apply      does it (needs a clean git tree), then verifies the result
    python tools/restructure_web.py --apply --tests    also runs the test suite before and after and compares

What it guarantees (and checks after the move):
  * every route keeps its name and its address (compared before and after)
  * every import points at something that exists, and no name in a new module is undefined
  * every template name used in the code exists on disk
Run it on a branch. To undo:  git reset --hard && git clean -fd apps/web
"""
from __future__ import annotations

import argparse
import ast
import builtins
import json
import os
import re
import subprocess
import symtable
import sys
import tempfile
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

PKG = "apps.web"

# --------------------------------------------------------------------------------------------- the plan
# Files that are split up. Each target lists the top-level names that move there ("*" = everything else).
SPLITS = OrderedDict([
    ("views.py", OrderedDict([
        ("access.py", ["employee_required"]),
        ("views/dashboard.py", ["dashboard"]),
        ("views/account.py", ["PasswordChangeView"]),
        ("views/leave.py", ["decorate_leaves", "leave_list", "leave_apply", "leave_preview", "leave_cancel"]),
        ("views/approvals.py", ["SERVICE_ERRORS", "_approval", "approvals_inbox", "approval_count",
                                "approval_row", "approval_reject_form", "approval_decide"]),
        ("views/attendance.py", ["_parse_month", "_parse_day", "_day_rows", "attendance", "attendance_row",
                                 "attendance_correct"]),
    ])),
    ("hr_views.py", OrderedDict([("access.py", ["hr_perm"]), ("views/staff.py", "*")])),
    ("forms.py", OrderedDict([
        ("forms/common.py", ["date_input", "time_input"]),
        ("forms/leave.py", ["LeaveApplyForm"]),
        ("forms/attendance.py", ["_S", "CORRECTION_STATUSES", "CorrectionForm"]),
    ])),
])

# Files that move whole.
WHOLE = OrderedDict([
    ("company_views.py", "views/companies.py"),
    ("org_views.py", "views/organization.py"),
    ("compliance_views.py", "views/compliance.py"),
    ("hr_forms.py", "forms/staff.py"),
    ("company_forms.py", "forms/companies.py"),
    ("org_forms.py", "forms/organization.py"),
    ("compliance_forms.py", "forms/compliance.py"),
])

# Names that change on the way (a helper used by two modules should not be private).
RENAMES = {"_decorate_leaves": "decorate_leaves"}

TESTS = OrderedDict([
    ("tests.py", "tests/test_self_service.py"),
    ("test_staff.py", "tests/test_staff.py"),
    ("test_companies.py", "tests/test_companies.py"),
    ("test_org.py", "tests/test_organization.py"),
    ("test_compliance.py", "tests/test_compliance.py"),
    ("test_datepickers.py", "tests/test_datepickers.py"),
    ("test_templates.py", "tests/test_templates.py"),
])

# Templates (relative to apps/web/templates/web/). Shared pages stay at the top.
TEMPLATES = OrderedDict([
    ("leave_list.html", "leave/list.html"), ("leave_apply.html", "leave/apply.html"),
    ("_leave_row.html", "leave/_row.html"), ("_leave_preview.html", "leave/_preview.html"),
    ("approvals.html", "approvals/inbox.html"), ("_approval_row.html", "approvals/_row.html"),
    ("_approval_reject_form.html", "approvals/_reject_form.html"), ("_approval_done.html", "approvals/_done.html"),
    ("attendance.html", "attendance/month.html"), ("_att_row.html", "attendance/_row.html"),
    ("_att_correction_form.html", "attendance/_correction_form.html"),
    ("employee_list.html", "staff/list.html"), ("_employee_table.html", "staff/_table.html"),
    ("employee_form.html", "staff/form.html"), ("_employee_company_fields.html", "staff/_company_fields.html"),
    ("employee_detail.html", "staff/detail.html"),
    ("company_list.html", "companies/list.html"), ("company_delete.html", "companies/delete.html"),
    ("department_list.html", "organization/departments.html"),
    ("designation_list.html", "organization/designations.html"), ("org_delete.html", "organization/delete.html"),
    ("compliance_dashboard.html", "compliance/dashboard.html"), ("compliance_documents.html", "compliance/documents.html"),
    ("compliance_detail.html", "compliance/detail.html"), ("compliance_costs.html", "compliance/costs.html"),
    ("compliance_types.html", "compliance/types.html"), ("_compliance_doc_table.html", "compliance/_doc_table.html"),
])
KEEP_TEMPLATES = {"base.html", "login.html", "no_employee.html", "password_change.html", "form_page.html",
                  "form_layout.html", "dashboard.html"}

DOCSTRINGS = {
    "access.py": "Who may open a page: permission checks and the employee-record check shared by every view module.",
    "views/dashboard.py": "The employee's home page.",
    "views/account.py": "Password change.",
    "views/leave.py": "Self-service leave: apply, list, cancel and the live day-count preview.",
    "views/approvals.py": "The approvals inbox: approve, reject and the menu badge.",
    "views/attendance.py": "My attendance: the monthly view and correction requests.",
    "views/staff.py": "Staff records: list, add, edit, change assignment and logins (HR).",
    "views/companies.py": "Companies: list, add, edit, deactivate and delete.",
    "views/organization.py": "Departments and designations.",
    "views/compliance.py": "Compliance: documents, renewals, payments, costs and document types.",
    "forms/common.py": "Field helpers shared by every form.",
    "forms/leave.py": "Leave application form.",
    "forms/attendance.py": "Attendance correction form.",
    "forms/staff.py": "Staff forms: registration, personal details and assignment changes.",
    "forms/companies.py": "Company form.",
    "forms/organization.py": "Department and designation forms.",
    "forms/compliance.py": "Compliance forms: documents, renewals, payments and document types.",
}


README = """# apps/web

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
"""


def mod_of(rel: str) -> str:
    return f"{PKG}." + rel[:-3].replace("/", ".")


HOMES: dict[str, dict] = {}
for _f, _targets in SPLITS.items():
    _names, _star = {}, None
    for _t, _spec in _targets.items():
        if _spec == "*":
            _star = mod_of(_t)
        else:
            for _n in _spec:
                _names[_n] = mod_of(_t)
    HOMES[_f[:-3]] = {"names": _names, "star": _star}
WHOLE_HOMES = {f[:-3]: mod_of(t) for f, t in WHOLE.items()}
MOVED = set(HOMES) | set(WHOLE_HOMES)


def new_home(old_mod: str, name: str):
    if old_mod in HOMES:
        return HOMES[old_mod]["names"].get(name, HOMES[old_mod]["star"])
    return WHOLE_HOMES.get(old_mod)


# ------------------------------------------------------------------------------------------- imports
@dataclass
class Imp:
    kind: str                                  # "from" or "import"
    module: str
    names: list                                # [(name, asname)]


def absolute_module(node: ast.ImportFrom, pkg: str) -> str:
    if node.level == 0:
        return node.module or ""
    base = pkg.split(".")
    if node.level > 1:
        base = base[: len(base) - (node.level - 1)]
    return ".".join(base + ([node.module] if node.module else []))


def fmt_alias(n, a):
    return f"{n} as {a}" if a else n


def fmt_from(module: str, names: list, indent: str = "") -> list:
    one = f"{indent}from {module} import " + ", ".join(fmt_alias(n, a) for n, a in names)
    if len(one) <= 99:
        return [one]
    return [f"{indent}from {module} import ("] + [f"{indent}    {fmt_alias(n, a)}," for n, a in names] + [f"{indent})"]


def rewrite_imports(src: str, pkg: str, label: str, problems: list) -> str:
    """Make every intra-package import absolute and point it at the new home of what it imports."""
    tree = ast.parse(src)
    lines = src.splitlines(keepends=True)
    edits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        full = absolute_module(node, pkg)
        out = None
        if full == PKG:
            bad = [a.name for a in node.names if a.name in MOVED]
            if bad:
                problems.append(f"{label}:{node.lineno}: imports the module(s) {bad} as modules; change it by hand")
                continue
            if node.level:
                out = fmt_from(PKG, [(a.name, a.asname) for a in node.names])
        elif full.startswith(PKG + "."):
            sub = full[len(PKG) + 1:]
            first = sub.split(".")[0]
            if first in MOVED and sub == first:
                groups: OrderedDict = OrderedDict()
                for a in node.names:
                    if a.name == "*":
                        problems.append(f"{label}:{node.lineno}: 'import *' from {full} cannot be moved automatically")
                        break
                    dest = new_home(first, a.name)
                    if dest is None:
                        problems.append(f"{label}:{node.lineno}: '{a.name}' is imported from {full} but is not in the plan")
                        break
                    groups.setdefault(dest, []).append((a.name, a.asname))
                else:
                    out = []
                    for dest, names in groups.items():
                        out += fmt_from(dest, names)
            elif node.level:
                out = fmt_from(full, [(a.name, a.asname) for a in node.names])
        if out:
            indent = " " * node.col_offset
            edits.append((node.lineno - 1, node.end_lineno, [indent + out[0].lstrip()] + out[1:] if indent else out))
    for start, end, new in sorted(edits, key=lambda e: -e[0]):
        ind = " " * (len(lines[start]) - len(lines[start].lstrip(" "))) if lines[start].startswith(" ") else ""
        lines[start:end] = [(ind + l if i and ind else l) + "\n" for i, l in enumerate(new)]
    return "".join(lines)


def collect_imports(tree: ast.Module):
    """Top-level imports as structures, plus {bound name: Imp-with-one-name}."""
    imps, bound = [], {}
    for n in tree.body:
        if isinstance(n, ast.ImportFrom):
            if n.module == "__future__":
                imps.append(Imp("from", "__future__", [(a.name, a.asname) for a in n.names]))
                continue
            module = ("." * n.level) + (n.module or "")
            imp = Imp("from", module, [(a.name, a.asname) for a in n.names])
            imps.append(imp)
            for a in n.names:
                bound[a.asname or a.name] = Imp("from", module, [(a.name, a.asname)])
        elif isinstance(n, ast.Import):
            imp = Imp("import", "", [(a.name, a.asname) for a in n.names])
            imps.append(imp)
            for a in n.names:
                bound[a.asname or a.name.split(".")[0]] = Imp("import", "", [(a.name, a.asname)])
    return imps, bound


def merge_imports(imports: list) -> list:
    merged: OrderedDict = OrderedDict()
    for imp in imports:
        if imp.kind == "from":
            cur = merged.setdefault(("from", imp.module), [])
            for na in imp.names:
                if na not in cur:
                    cur.append(na)
        else:
            for na in imp.names:
                merged.setdefault(("import", na[0]), [na])
    return [Imp(k, m, names) for (k, m), names in merged.items()]


def render_imports(imports: list) -> str:
    imports = merge_imports(imports)
    std = set(sys.stdlib_module_names)

    def group(imp):
        root = (imp.module or imp.names[0][0]).split(".")[0]
        if imp.module == "__future__":
            return 0
        if root in std:
            return 1
        if root in ("apps", "config"):
            return 3
        return 2

    out = []
    for g in (0, 1, 2, 3):
        items = [i for i in imports if group(i) == g]
        if not items:
            continue
        # plain "import x" first, then "from x import ...", each sorted
        items.sort(key=lambda i: (i.kind != "import", (i.module or i.names[0][0]).lower()))
        for imp in items:
            if imp.kind == "import":
                out.append("import " + ", ".join(fmt_alias(n, a) for n, a in imp.names))
            else:
                names = sorted(imp.names, key=lambda na: (not na[0][:1].isupper() is False, na[0].lower())) \
                    if False else sorted(imp.names, key=lambda na: na[0].lower())
                out += fmt_from(imp.module, names)
        out.append("")
    return "\n".join(out).rstrip("\n") + "\n" if out else ""


# ------------------------------------------------------------------------------- scope analysis
def globals_of(tbl) -> set:
    names = set()
    for s in tbl.get_symbols():
        if s.is_global() and s.is_referenced():
            names.add(s.get_name())
    for ch in tbl.get_children():
        names |= globals_of(ch)
    return names


def header_names(node) -> set:
    parts = []
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        a = node.args
        parts += node.decorator_list + a.defaults + [d for d in a.kw_defaults if d]
        for arg in a.posonlyargs + a.args + a.kwonlyargs + ([a.vararg] if a.vararg else []) + ([a.kwarg] if a.kwarg else []):
            if arg.annotation:
                parts.append(arg.annotation)
        if node.returns:
            parts.append(node.returns)
    elif isinstance(node, ast.ClassDef):
        parts += node.decorator_list + node.bases + [k.value for k in node.keywords]
    names = set()
    for p in parts:
        names |= {n.id for n in ast.walk(p) if isinstance(n, ast.Name)}
    return names


def undefined_names(src: str) -> set:
    """Names a module uses but never defines or imports (a NameError waiting to happen)."""
    top = symtable.symtable(src, "<module>", "exec")
    defined = {s.get_name() for s in top.get_symbols()
               if s.is_assigned() or s.is_imported() or s.is_namespace() or s.is_parameter()}
    used = {s.get_name() for s in top.get_symbols() if s.is_referenced()}
    for ch in top.get_children():
        used |= globals_of(ch)
    return {n for n in used - defined if not hasattr(builtins, n) and n not in ("__file__", "__name__", "__doc__")}


# -------------------------------------------------------------------------------------- splitting
def split_source(src: str, old_mod: str, targets: OrderedDict, label: str, problems: list) -> dict:
    for old, new in RENAMES.items():
        src = re.sub(rf"\b{re.escape(old)}\b", new, src)
    src = rewrite_imports(src, PKG, label, problems)
    tree = ast.parse(src)
    lines = src.splitlines(keepends=True)
    top = symtable.symtable(src, label, "exec")
    tables = {c.get_name(): c for c in top.get_children() if c.get_type() in ("function", "class")}
    imps, bound = collect_imports(tree)

    spec_of, star = {}, None
    for t, spec in targets.items():
        if spec == "*":
            star = t
        else:
            for n in spec:
                spec_of[n] = t

    banner = re.compile(r"^#\s*-{8,}.*\n?$")
    items, prev_end = [], 0
    for idx, n in enumerate(tree.body):
        end = n.end_lineno if idx < len(tree.body) - 1 else len(lines)
        first = min([n.lineno] + [d.lineno for d in getattr(n, "decorator_list", [])])
        lead = "".join(l for l in lines[prev_end:first - 1] if not banner.match(l))
        lead = re.sub(r"\n{4,}", "\n\n\n", lead)
        text = lead + "".join(lines[first - 1:end])
        prev_end = end
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            continue
        if idx == 0 and isinstance(n, ast.Expr) and isinstance(getattr(n, "value", None), ast.Constant) \
                and isinstance(n.value.value, str):
            continue                                              # the old module docstring
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names = [n.name]
            used = header_names(n) | (globals_of(tables[n.name]) if n.name in tables else set())
        elif isinstance(n, ast.Assign) and all(isinstance(t, ast.Name) for t in n.targets):
            names = [t.id for t in n.targets]
            used = {x.id for x in ast.walk(n.value) if isinstance(x, ast.Name)}
        elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
            names = [n.target.id]
            used = {x.id for x in ast.walk(n) if isinstance(x, ast.Name)} - {n.target.id}
        else:
            problems.append(f"{label}:{n.lineno}: a top-level statement the plan does not understand "
                            f"({type(n).__name__}); move it by hand first")
            continue
        tgts = {spec_of.get(x, star) for x in names}
        if len(tgts) != 1 or None in tgts:
            problems.append(f"{label}:{n.lineno}: '{', '.join(names)}' is not in the plan for {old_mod}.py "
                            f"(add it to SPLITS in this tool, or tell me)")
            continue
        items.append({"names": names, "text": text, "used": used, "target": tgts.pop()})
    if problems:
        return {}

    defined = {x for it in items for x in it["names"]}
    home = {x: it["target"] for it in items for x in it["names"]}
    result: dict = {}
    for t in targets:
        mine = [it for it in items if it["target"] == t]
        if not mine:
            continue
        used = set().union(*(it["used"] for it in mine))
        keep = OrderedDict()
        for imp in imps:
            if imp.module == "__future__":
                keep[id(imp)] = imp
                continue
            for na in imp.names:
                b = na[1] or (na[0].split(".")[0] if imp.kind == "import" else na[0])
                if b in used and b not in defined:
                    keep.setdefault(id(imp), Imp(imp.kind, imp.module, []))
                    if na not in keep[id(imp)].names:
                        keep[id(imp)].names.append(na)
        cross = OrderedDict()
        for it in mine:
            for x in sorted(it["used"] & defined):
                if home[x] != t:
                    cross.setdefault(mod_of(home[x]), []).append((x, None))
        extra = [Imp("from", m, sorted(set(ns))) for m, ns in cross.items()]
        deps = {m for m in cross}
        result[t] = {"imports": [i for i in keep.values() if i.names] + extra, "chunks": [it["text"] for it in mine],
                     "deps": deps, "names": [x for it in mine for x in it["names"]]}
    # a cycle between the new modules would fail at import time
    mods = {mod_of(t): t for t in result}
    for t, info in result.items():
        for d in info["deps"]:
            if d in mods and mod_of(t) in result[mods[d]]["deps"]:
                problems.append(f"{label}: {t} and {mods[d]} would import each other; the plan needs another split")
    return result


def compose(target: str, imports: list, chunks: list) -> str:
    doc = DOCSTRINGS.get(target)
    parts = []
    if doc:
        parts.append(f'"""{doc}"""\n')
    imp = render_imports(imports)
    if imp:
        parts.append(imp)
    body = "".join(chunks).strip("\n") + "\n"
    return "\n".join(parts) + ("\n\n" if parts else "") + body


# ------------------------------------------------------------------------------------------- urls
def build_urls(src: str, problems: list):
    tree = ast.parse(src)
    alias, others = {}, {}
    for n in tree.body:
        if isinstance(n, ast.ImportFrom):
            full = absolute_module(n, PKG)
            for a in n.names:
                bound = a.asname or a.name
                if full == PKG and a.name in MOVED:
                    alias[bound] = a.name
                else:
                    others[bound] = Imp("from", full, [(x.name, x.asname) for x in n.names])
    app_name, routes = None, None
    for n in tree.body:
        if isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name) and t.id == "app_name":
                    app_name = ast.literal_eval(n.value)
                if isinstance(t, ast.Name) and t.id == "urlpatterns":
                    routes = n.value
    if routes is None or not isinstance(routes, ast.List):
        problems.append("urls.py: could not find a plain 'urlpatterns = [...]' list")
        return None
    starts = [0]
    for line in src.splitlines(keepends=True):
        starts.append(starts[-1] + len(line))
    off = lambda ln, col: starts[ln - 1] + col
    by_feature: OrderedDict = OrderedDict()
    funcs_used = set()
    for e in routes.elts:
        if not (isinstance(e, ast.Call) and isinstance(e.func, ast.Name) and e.func.id in ("path", "re_path")):
            problems.append(f"urls.py:{e.lineno}: a route the tool cannot read (only path()/re_path() calls)")
            continue
        funcs_used.add(e.func.id)
        view = e.args[1]
        ref = next((x for x in ast.walk(view) if isinstance(x, ast.Attribute) and isinstance(x.value, ast.Name)), None)
        if ref is None:
            problems.append(f"urls.py:{e.lineno}: cannot tell which view this route uses")
            continue
        root = ref.value.id
        extra = None
        if root in alias:
            dest = new_home(alias[root], ref.attr)
            if not dest:
                problems.append(f"urls.py:{e.lineno}: view '{root}.{ref.attr}' is not in the plan")
                continue
            feature = dest.rsplit(".", 1)[1]
            if dest.startswith(f"{PKG}.views.") is False:
                problems.append(f"urls.py:{e.lineno}: '{ref.attr}' moves to {dest}, which is not a views module")
                continue
        elif root == "auth_views":
            feature, extra = "account", others.get("auth_views")
        else:
            problems.append(f"urls.py:{e.lineno}: view '{root}.{ref.attr}' is from somewhere the tool does not know")
            continue
        s, t = off(e.lineno, e.col_offset), off(e.end_lineno, e.end_col_offset)
        a, b = off(ref.lineno, ref.col_offset), off(ref.end_lineno, ref.end_col_offset)
        text = src[s:a] + (f"{feature}.{ref.attr}" if root in alias else src[a:b]) + src[b:t]
        slot = by_feature.setdefault(feature, {"routes": [], "extras": []})
        slot["routes"].append(text)
        if extra and extra not in slot["extras"]:
            slot["extras"].append(extra)
    if problems:
        return None
    files = {}
    for feature, slot in by_feature.items():
        used = ", ".join(sorted(funcs_used))
        imports = [Imp("from", "django.urls", [(f, None) for f in sorted(funcs_used)])] + slot["extras"]
        if any(f"{feature}." in r for r in slot["routes"]):
            imports.append(Imp("from", f"{PKG}.views", [(feature, None)]))
        body = "urlpatterns = [\n" + "".join(f"    {r.strip()},\n" for r in slot["routes"]) + "]\n"
        files[f"urls/{feature}.py"] = render_imports(imports) + f"\n\n{body}"
    names = list(by_feature)
    init = ('"""URLs for the web interface, one file per feature. Names stay under the "web:" namespace."""\n\n'
            f"from . import {', '.join(sorted(names))}\n\n"
            f"app_name = {json.dumps(app_name)}\n\nurlpatterns = [\n" + "".join(f"    *{n}.urlpatterns,\n" for n in names) + "]\n")
    files["urls/__init__.py"] = init
    return files


# ---------------------------------------------------------------------------------------- helpers
class Ctx:
    def __init__(self, root: Path, use_git: bool):
        self.root, self.web, self.use_git = root, root / "apps" / "web", use_git
        self.templates = self.web / "templates" / "web"

    def run_git(self, *args):
        subprocess.run(["git", *args], cwd=self.root, check=True, capture_output=True, text=True)

    def move(self, src: Path, dst: Path):
        dst.parent.mkdir(parents=True, exist_ok=True)
        if self.use_git:
            self.run_git("mv", str(src.relative_to(self.root)), str(dst.relative_to(self.root)))
        else:
            src.rename(dst)

    def write(self, path: Path, text: str):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
        if self.use_git:
            self.run_git("add", str(path.relative_to(self.root)))

    def remove(self, path: Path):
        if self.use_git:
            self.run_git("rm", "-q", "-f", str(path.relative_to(self.root)))
        else:
            path.unlink()


def read(path: Path) -> str:
    with open(path, encoding="utf-8") as fh:      # universal newlines: a Windows CRLF file reads as LF
        return fh.read()


def py_files(root: Path):
    for base in ("apps", "config"):
        for p in (root / base).rglob("*.py"):
            if "migrations" in p.parts or "__pycache__" in p.parts:
                continue
            yield p


URL_SNAPSHOT = r'''
import json, os, re
from django.urls import get_resolver, resolve
def walk(patterns, prefix="", ns=""):
    rows = []
    for p in patterns:
        if hasattr(p, "url_patterns"):
            rows += walk(p.url_patterns, prefix + str(p.pattern), ns + (p.namespace + ":" if p.namespace else ""))
        else:
            rows.append((ns + p.name if p.name else None, prefix + str(p.pattern)))
    return rows
SAMPLES = {"int": "1", "str": "2026-01-01", "slug": "x", "uuid": "00000000-0000-0000-0000-000000000001", "path": "x"}
def sample(route):
    return re.sub(r"<(?:(\w+):)?\w+>", lambda m: SAMPLES.get(m.group(1) or "str", "x"), route)
out = []
for name, route in walk(get_resolver().url_patterns):
    entry = {"name": name, "route": route}
    if "^" not in route and "(" not in route:
        try:
            entry["resolves_to"] = resolve("/" + sample(route)).view_name
        except Exception as exc:
            entry["resolves_to"] = "ERROR " + type(exc).__name__
    out.append(entry)
json.dump(out, open(os.environ["RESTRUCTURE_OUT"], "w"), indent=1)
'''


def url_snapshot(root: Path):
    if not (root / "manage.py").exists():
        return None
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    env = dict(os.environ, RESTRUCTURE_OUT=path)
    r = subprocess.run([sys.executable, "manage.py", "shell", "-c", URL_SNAPSHOT], cwd=root, env=env,
                       capture_output=True, text=True)
    try:
        data = json.load(open(path))
    except Exception:
        print("   (could not read the URL table: " + (r.stderr.strip().splitlines() or ["?"])[-1] + ")")
        return None
    finally:
        os.unlink(path)
    return data


def missing_templates(ctx: Ctx) -> dict:
    """{template name: [files that mention it]} for names that do not exist on disk."""
    found: dict = {}
    for p in list(py_files(ctx.root)) + list(ctx.web.rglob("*.html")):
        for m in re.finditer(r"[\"'](web/[\w/\-.]+\.html)[\"']", read(p)):
            if not (ctx.web / "templates" / m.group(1)).exists():
                found.setdefault(m.group(1), []).append(str(p.relative_to(ctx.root).as_posix()))
    return found


def run_tests(root: Path):
    r = subprocess.run([sys.executable, "manage.py", "test", "apps"], cwd=root, capture_output=True, text=True)
    out = r.stderr + r.stdout
    m = re.search(r"Ran (\d+) tests?", out)
    ok = bool(re.search(r"^OK", out, re.M))
    return (int(m.group(1)) if m else None), ok, out


# ----------------------------------------------------------------------------------------- planning
def plan(ctx: Ctx):
    """Work out every change in memory. Returns (writes, moves, removes, problems, notes)."""
    problems, notes = [], []
    writes: dict = {}          # path -> text
    moves: list = []           # (src, dst)
    removes: list = []

    # (only real packages count: an undo can leave empty folders that contain nothing but __pycache__)
    done = [d for d in ("views", "forms", "urls") if (ctx.web / d / "__init__.py").exists()]
    if done:
        problems.append(f"apps/web already has {', '.join(d + '/' for d in done)} as packages: the move looks done already")
        return writes, moves, removes, problems, notes

    # --- split files
    access_imports, access_chunks = [], []
    produced: dict = {}
    for fname, targets in SPLITS.items():
        path = ctx.web / fname
        if not path.exists():
            notes.append(f"{fname}: not found, skipped")
            continue
        sub: list = []
        res = split_source(read(path), fname[:-3], targets, f"apps/web/{fname}", sub)
        problems += sub
        if not res:
            continue
        removes.append(path)
        for t, info in res.items():
            if t == "access.py":
                access_imports += info["imports"]
                access_chunks += info["chunks"]
            else:
                produced[t] = (info["imports"], info["chunks"])
    if access_chunks:
        produced["access.py"] = (access_imports, access_chunks)
    for t, (imps, chunks) in produced.items():
        writes[ctx.web / t] = compose(t, imps, chunks)

    # --- whole moves (python)
    for old, new in list(WHOLE.items()) + list(TESTS.items()):
        path = ctx.web / old
        if not path.exists():
            notes.append(f"{old}: not found, skipped")
            continue
        moves.append((path, ctx.web / new))

    # --- files that stay but import moved modules, anywhere in the project
    staying = []
    for p in py_files(ctx.root):
        rel = p.relative_to(ctx.web).as_posix() if ctx.web in p.parents else None
        if rel in SPLITS or rel == "urls.py":
            continue
        staying.append(p)
    rewritten: dict = {}
    for p in staying:
        text = read(p)
        label = p.relative_to(ctx.root).as_posix()
        pkg = ".".join(p.relative_to(ctx.root).with_suffix("").parts[:-1])
        sub = []
        new = rewrite_imports(text, pkg, label, sub)
        problems += sub
        if new != text:
            rewritten[p] = new
    # package markers
    for pkgdir in ("views", "forms", "tests"):
        if not any(str(w).startswith(str(ctx.web / pkgdir) + os.sep) for w in list(writes) + [d for _, d in moves]):
            continue
        init = ctx.web / pkgdir / "__init__.py"
        if not init.exists():
            writes[init] = ""

    if not (ctx.web / "README.md").exists():
        writes[ctx.web / "README.md"] = README

    # --- urls
    urls = ctx.web / "urls.py"
    if urls.exists():
        sub = []
        files = build_urls(read(urls), sub)
        problems += sub
        if files:
            removes.append(urls)
            for rel, text in files.items():
                writes[ctx.web / rel] = text
    else:
        notes.append("urls.py: not found, skipped")

    # --- templates
    tmoves = []
    if ctx.templates.exists():
        for old, new in TEMPLATES.items():
            if (ctx.templates / old).exists():
                tmoves.append((ctx.templates / old, ctx.templates / new))
        known = set(TEMPLATES) | KEEP_TEMPLATES
        for p in sorted(ctx.templates.glob("*.html")):
            if p.name not in known:
                notes.append(f"templates/web/{p.name}: not in the plan, left where it is")
    moves += tmoves
    return writes, moves, removes, problems, notes, rewritten, tmoves


def template_string_edits(ctx: Ctx, tmoves, planned: dict):
    """Rename template names inside code and templates, including files this run is about to create.
    `planned` maps path -> the text that will be written there. Returns {path: new text} for files that change."""
    pairs = [(f"web/{s.relative_to(ctx.templates).as_posix()}", f"web/{d.relative_to(ctx.templates).as_posix()}")
             for s, d in tmoves]
    edits = {}
    candidates = {p for p in py_files(ctx.root)} | set(ctx.web.rglob("*.html")) | set(planned)
    for p in candidates:
        if p in planned:
            text = planned[p]
        elif p.exists():
            text = read(p)
        else:
            continue
        new = text
        for old, nw in pairs:
            new = re.sub(rf"([\"']){re.escape(old)}\1", rf"\1{nw}\1", new)
        if new != text:
            edits[p] = new
    return edits


# --------------------------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="make the changes (default is a dry run)")
    ap.add_argument("--root", default=".", help="project folder (the one with manage.py)")
    ap.add_argument("--allow-dirty", action="store_true", help="skip the clean-git-tree safety check")
    ap.add_argument("--no-git", action="store_true", help="do not use git (no history, no safety check)")
    ap.add_argument("--tests", action="store_true", help="run the test suite before and after")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    if not (root / "apps" / "web").is_dir():
        sys.exit("Run this from the project folder (the one with manage.py and apps/web).")
    use_git = not args.no_git and (root / ".git").exists()
    ctx = Ctx(root, use_git)

    if args.apply and use_git and not args.allow_dirty:
        dirty = subprocess.run(["git", "status", "--porcelain"], cwd=root, capture_output=True, text=True).stdout.strip()
        if dirty:
            sys.exit("The git tree has uncommitted changes. Commit or stash them first (so the move can be undone "
                     "cleanly), or use --allow-dirty.\n" + dirty[:600])

    res = plan(ctx)
    writes, moves, removes, problems, notes = res[:5]
    rewritten, tmoves = (res[5], res[6]) if len(res) > 5 else ({}, [])

    # fix up test_templates.py (it scans the template folder, which now has subfolders)
    moved_dst = {s: d for s, d in moves}
    for src, dst in moves:
        if src.name == "test_templates.py":
            t = read(src)
            t2 = t.replace('Path(__file__).parent / "templates" / "web"', 'Path(__file__).resolve().parent.parent / "templates" / "web"')
            t2 = t2.replace('folder.glob("*.html")', 'folder.rglob("*.html")')
            if t2 == t:
                notes.append("tests/test_templates.py: could not adjust the template folder path; check it by hand")
            else:
                rewritten[src] = t2

    # python moves: apply the import rewrite to the moved files' text
    for src, dst in moves:
        if src.suffix == ".py" and src not in rewritten:
            sub = []
            label = src.relative_to(ctx.root).as_posix()
            new = rewrite_imports(read(src), PKG, label, sub)
            problems += sub
            if new != read(src):
                rewritten[src] = new
    # template names
    texts_after = {}
    for p, t in list(rewritten.items()) + list(writes.items()):
        texts_after[p] = t
    tedits = template_string_edits(ctx, tmoves, texts_after)

    # --- checks on everything we are about to write
    for path, text in list(writes.items()) + list(rewritten.items()) + list(tedits.items()):
        if path.suffix != ".py":
            continue
        text = tedits.get(path, text)
        try:
            ast.parse(text)
        except SyntaxError as exc:
            problems.append(f"{path.relative_to(ctx.root)}: would not be valid Python ({exc})")
            continue
        if path in writes and path.name != "__init__.py":
            missing = undefined_names(text)
            if missing:
                problems.append(f"{path.relative_to(ctx.root)}: would use names it never defines: {sorted(missing)}")

    # --- report
    rel = lambda p: p.relative_to(ctx.root).as_posix()
    print("PLAN")
    print(f"  split into new files : {sum(1 for p in writes if p.suffix == '.py' and p.name != '__init__.py')}")
    for p in sorted(writes, key=rel):
        if p.suffix == ".py" and p.name != "__init__.py":
            print("     +", rel(p))
    print(f"  files moved          : {len(moves)}")
    for s, d in moves:
        print(f"     {rel(s)}  ->  {rel(d)}")
    print(f"  old files removed    : {', '.join(rel(p) for p in removes) or '-'}")
    print(f"  files with imports rewritten: {len([p for p in rewritten if p not in moved_dst])}")
    print(f"  template names rewritten in : {len(tedits)} file(s)")
    for n in notes:
        print("  note:", n)
    if problems:
        print("\nPROBLEMS (nothing was changed):")
        for p in problems:
            print("  -", p)
        sys.exit(1)
    if not args.apply:
        print("\nDry run only. Run again with --apply to make these changes.")
        return

    # ---- before snapshot
    missing_before = set(missing_templates(ctx))
    before = url_snapshot(root)
    base = None
    if args.tests:
        print("\nrunning the tests before the move ...")
        base = run_tests(root)
        print(f"   before: {base[0]} tests, {'OK' if base[1] else 'FAILED'}")
        if not base[1]:
            sys.exit("The tests are not green before the move. Fix them first.\n" + base[2][-1500:])

    # ---- apply
    for src, dst in moves:
        ctx.move(src, dst)
    for path, text in rewritten.items():
        target = moved_dst.get(path, path)
        ctx.write(target, tedits.pop(path, text) if path in tedits else text)
    for path, text in writes.items():
        ctx.write(path, tedits.get(path, text))
    for path, text in tedits.items():
        if path in rewritten or path in writes:
            continue
        ctx.write(moved_dst.get(path, path), text)
    for path in removes:
        if path.exists():
            ctx.remove(path)
    for pycache in ctx.web.rglob("__pycache__"):
        pass

    # ---- verify
    print("\nVERIFYING")
    failures = []
    # 1. every apps.web import resolves
    for p in py_files(ctx.root):
        tree = ast.parse(read(p))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(PKG):
                fs = ctx.root / Path(*node.module.split("."))
                file = fs.with_suffix(".py") if fs.with_suffix(".py").exists() else fs / "__init__.py"
                if not file.exists():
                    failures.append(f"{rel(p)}:{node.lineno}: imports {node.module}, which does not exist")
                    continue
                exported = set()
                t = ast.parse(read(file))
                for n in t.body:
                    if isinstance(n, (ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef)):
                        exported.add(n.name)
                    elif isinstance(n, ast.Assign):
                        exported |= {x.id for x in n.targets if isinstance(x, ast.Name)}
                    elif isinstance(n, (ast.Import, ast.ImportFrom)):
                        exported |= {(a.asname or a.name).split(".")[0] for a in n.names}
                for a in node.names:
                    if a.name not in exported and not (fs / f"{a.name}.py").exists():
                        failures.append(f"{rel(p)}:{node.lineno}: '{a.name}' is not defined in {node.module}")
    print("  imports resolve:", "yes" if not failures else f"NO ({len(failures)})")
    # 2. template names exist (names that were already missing before the move are not the move's fault)
    now_missing = missing_templates(ctx)
    broken = {n: f for n, f in now_missing.items() if n not in missing_before}
    for n, f in broken.items():
        failures.append(f"template '{n}' is used in {', '.join(sorted(set(f)))} but does not exist")
    print("  template names exist:", "yes" if not broken else f"NO ({len(broken)})")
    for n in sorted(set(now_missing) & missing_before):
        print(f"     (note: '{n}' was already missing before the move)")
    # 2b. every new package has its __init__.py (without it Python cannot import it and tests are not discovered)
    no_init = [d for d in ("views", "forms", "urls", "tests")
               if (ctx.web / d).is_dir() and list((ctx.web / d).glob("*.py")) and not (ctx.web / d / "__init__.py").exists()]
    failures += [f"apps/web/{d}/ has no __init__.py" for d in no_init]
    print("  packages complete:", "yes" if not no_init else "NO")
    # 3. old files gone
    left = [n for n in list(SPLITS) + ["urls.py"] if (ctx.web / n).exists()]
    failures += [f"apps/web/{n} is still there" for n in left]
    print("  old files gone:", "yes" if not left else "NO")
    # 4. routes unchanged
    after = url_snapshot(root)
    if before is not None and after is not None:
        key = lambda e: (e["name"] or "", e["route"])
        same = sorted(before, key=key) == sorted(after, key=key)
        print(f"  routes unchanged ({len(after)} routes, names, addresses and resolution):", "yes" if same else "NO")
        if not same:
            b, a = {key(e): e for e in before}, {key(e): e for e in after}
            for k in sorted(set(b) | set(a)):
                if b.get(k) != a.get(k):
                    failures.append(f"route changed: before={b.get(k)} after={a.get(k)}")
    else:
        print("  routes: not compared (no manage.py or Django not importable here)")
    # 5. tests
    if args.tests:
        print("running the tests after the move ...")
        post = run_tests(root)
        print(f"   after: {post[0]} tests, {'OK' if post[1] else 'FAILED'}")
        if post[0] != base[0] or not post[1]:
            failures.append(f"tests: {base[0]} before, {post[0]} after, ok={post[1]}\n" + post[2][-2500:])
    if failures:
        print("\nPROBLEMS AFTER THE MOVE:")
        for f in failures:
            print("  -", f)
        print("\nTo undo everything:  git reset --hard && git clean -fd apps/web")
        sys.exit(1)
    print("\nDone. Run the tests, look at 'git status', then commit.")


if __name__ == "__main__":
    main()
