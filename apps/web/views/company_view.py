"""The Company view: the setting itself, each company's colours, and the switch in the top bar."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_POST

from apps.organization import scope
from apps.organization.services import all_manageable_companies


def _back(request):
    """The Settings buttons post with HTMX: reload the page so the message, the switch and the colours all show."""
    if request.headers.get("HX-Request"):
        response = HttpResponse(status=200)
        response["HX-Refresh"] = "true"
        return response
    return redirect("web:settings_module", module="organization")


def _may_change(user):
    return user.has_perm(scope.EDIT_PERM)


@login_required
@require_POST
def company_view_set(request):
    if not _may_change(request.user):
        raise PermissionDenied
    mode = request.POST.get("company_view", "")
    if mode not in dict(scope.VIEWS):
        messages.error(request, "Choose Combined or Separate.")
        return _back(request)
    before = scope.current_view()
    scope.set_view(mode)
    if mode == before:
        messages.info(request, "Nothing changed.")
    elif mode == scope.SEPARATE:
        messages.success(request, "Company view is now Separate. Each login works in one company at a time.")
    else:
        messages.success(request, "Company view is now Combined. People see all their companies together.")
    return _back(request)


@login_required
@require_POST
def company_colours_set(request, pk):
    if not _may_change(request.user):
        raise PermissionDenied
    company = get_object_or_404(all_manageable_companies(request.user), pk=pk)
    if request.POST.get("reset"):
        theme_color = sidebar_color = ""
    else:
        ticked = bool(request.POST.get("by_tick"))      # the Settings table: only a ticked colour is kept
        try:
            theme_color = (scope.clean_colour(request.POST.get("theme_color"))
                           if not ticked or request.POST.get("theme_on") else "")
            sidebar_color = (scope.clean_colour(request.POST.get("sidebar_color"))
                             if not ticked or request.POST.get("sidebar_on") else "")
        except ValueError as exc:
            messages.error(request, f"{company.name}: {exc}")
            return _back(request)
    company.theme_color, company.sidebar_color = theme_color, sidebar_color
    company.save(update_fields=["theme_color", "sidebar_color"])
    messages.success(request, f"Colours saved for {company.name}." if (theme_color or sidebar_color)
                     else f"{company.name} now uses the standard colours.")
    return _back(request)


@login_required
@require_POST
def company_switch(request):
    """The switch in the top bar. Only companies the person has access to can be chosen."""
    raw = request.POST.get("company", "")
    if not raw.isdigit():
        raise Http404
    if request.company_view_separate:
        if scope.switch(request, int(raw)) is None:
            raise Http404
    return redirect("web:dashboard")
