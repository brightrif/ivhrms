from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse

from apps.configuration import access, registry
from apps.configuration import services as settings_service
from apps.configuration.services import SettingError
from apps.organization.services import companies_for


def _can_see(user, module):
    return user.has_perm(module.view_perm) or user.has_perm(module.edit_perm)


def _visible(user):
    return [m for m in registry.modules() if _can_see(user, m)]


def _field(key):
    return f"s__{key}"


@login_required
def settings_home(request):
    """Go to the first tab the person may see."""
    visible = _visible(request.user)
    if visible:
        return redirect("web:settings_module", module=visible[0].key)
    if request.user.is_superuser:
        return redirect("web:settings_access")
    raise PermissionDenied


@login_required
def settings_module(request, module):
    """One tab of the Settings page. Simple settings are saved together; dated policies have their own forms and
    appear here as custom sections."""
    mod = registry.get_module(module)
    if mod is None:
        raise Http404
    if not _can_see(request.user, mod):
        raise PermissionDenied
    can_edit = request.user.has_perm(mod.edit_perm)
    companies = list(companies_for(request.user))
    per_company = any(s.per_company for s in mod.settings)
    data = request.POST if request.method == "POST" else request.GET
    scope = None
    if per_company and data.get("company"):
        scope = next((c for c in companies if str(c.pk) == data.get("company")), None)
        if scope is None:
            raise Http404                      # a company the person cannot see is never quietly treated as "everyone"

    def editable(setting):
        return can_edit and (scope is None or setting.per_company)

    errors, posted = {}, {}
    if request.method == "POST":
        if not can_edit:
            raise PermissionDenied
        changes = []
        for setting in mod.settings:
            if not editable(setting):
                continue
            name = _field(setting.key)
            if scope is not None and request.POST.get(f"r__{setting.key}"):
                changes.append((setting, None, True))
                continue
            raw = (request.POST.get(name) is not None) if setting.kind == "bool" else request.POST.get(name, "")
            posted[setting.key] = raw
            try:
                changes.append((setting, settings_service.clean(setting, raw), False))
            except SettingError as exc:
                errors[setting.key] = str(exc)
        if not errors:
            saved = 0
            with transaction.atomic():
                for setting, value, revert in changes:
                    if revert:
                        saved += settings_service.use_default(setting.key, scope)
                        continue
                    if value == settings_service.get(setting.key, scope):
                        continue                                  # already what is in force: nothing to store
                    settings_service.set_value(setting.key, value, scope)
                    saved += 1
            messages.success(request, f"Saved {saved} change{'s' if saved != 1 else ''}." if saved else "Nothing changed.")
            suffix = f"?company={scope.pk}" if scope is not None else ""
            return redirect(reverse("web:settings_module", args=[mod.key]) + suffix)
        messages.error(request, "Nothing was saved. Fix the marked settings and try again.")

    sections = []
    for sec in mod.sections:
        rows = []
        for setting in sec.settings:
            value, source = settings_service.resolve(setting.key, scope)
            if setting.key in posted:
                value = posted[setting.key]
            rows.append({"setting": setting, "value": value, "source": source, "editable": editable(setting),
                         "error": errors.get(setting.key), "name": _field(setting.key), "revert": f"r__{setting.key}"})
        extra = sec.context(request.user) if sec.context else {}
        sections.append({"title": sec.title, "intro": sec.intro, "rows": rows, "template": sec.template, "extra": extra})
    return render(request, "web/settings/module.html", {
        "tabs": _visible(request.user), "module": mod, "sections": sections, "can_edit": can_edit,
        "companies": companies, "per_company": per_company, "scope": scope,
        "show_access": request.user.is_superuser, "tab": mod.key})


@login_required
def settings_access(request):
    """Who may do what. Superuser only. Reads and writes ordinary group permissions."""
    if not request.user.is_superuser:
        raise PermissionDenied
    if request.method == "POST":
        wanted = set()
        for item in request.POST.getlist("grant"):
            perm, _, group_id = item.rpartition("|")
            if perm and group_id.isdigit():
                wanted.add((perm, int(group_id)))
        granted, revoked = access.apply(request.user, wanted)
        messages.success(request, f"Saved. {granted} right{'s' if granted != 1 else ''} given, "
                                  f"{revoked} taken away." if (granted or revoked) else "Nothing changed.")
        return redirect("web:settings_access")
    groups, rows = access.grid()
    return render(request, "web/settings/access.html", {
        "tabs": _visible(request.user), "groups": groups, "rows": rows, "tab": "access", "show_access": True})
