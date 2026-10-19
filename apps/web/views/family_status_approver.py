"""Settings, Family Visas: who approves family status requests (the reporting manager, or one named person)."""
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render

from apps.dependants import status_approver as approver_service
from apps.organization.services import companies_for

from apps.web.access import hr_perm
from apps.web.forms.status_approver import StatusApproverForm
from apps.web.views.settings_hub import _visible


@hr_perm("configuration.view_settings_dependants")
def status_approver(request):
    can_edit = request.user.has_perm("configuration.edit_settings_dependants")
    form = StatusApproverForm(request.POST or None, user=request.user)
    if request.method == "POST":
        if not can_edit:
            raise PermissionDenied
        if form.is_valid():
            company, person = form.cleaned_data["company"], form.cleaned_data["approver"]
            try:
                if person is None:
                    changed = approver_service.clear_approver(request.user, company)
                    messages.success(request, f"The reporting manager approves family status for {company.name}."
                                     if changed else f"{company.name} already uses the reporting manager.")
                else:
                    approver_service.set_approver(request.user, company, person)
                    messages.success(request, f"{person.get_full_name() or person.get_username()} now approves every "
                                              f"family status request for {company.name}.")
            except approver_service.ApproverError as exc:
                form.add_error(None, str(exc))
            else:
                return redirect("web:family_status_approver")
    rows = [{"company": c, "approver": approver_service.approver_for(c)} for c in companies_for(request.user)]
    return render(request, "web/settings/status_approver.html", {
        "form": form, "rows": rows, "can_edit": can_edit, "tabs": _visible(request.user), "tab": "dependants",
        "show_access": request.user.is_superuser})
