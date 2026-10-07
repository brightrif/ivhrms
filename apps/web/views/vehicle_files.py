from pathlib import Path

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.text import slugify

from apps.audit import services as audit
from apps.vehicles import attachments
from apps.vehicles.files import VehicleFile
from apps.vehicles.services import VehicleError

from apps.web.access import hr_perm
from apps.web.forms.vehicle_files import AttachmentForm
from apps.web.forms.vehicle_upkeep import VoidForm
from apps.web.views.vehicles import _vehicle

LAYOUTS = {
    "file": [{"title": "File", "width": "col-lg-7", "rows": [["kind", "title"], ["file"]]}],
    "void": [{"title": "Remove", "width": "col-lg-6", "rows": [["reason"]]}],
}


def _form_page(request, form, title, back_url, layout, intro=""):
    return render(request, "web/form_layout.html", {
        "form": form, "title": title, "back_url": back_url, "layout": LAYOUTS[layout], "intro": intro})


def _may_see_some_file(user):
    return any(attachments.can_view(user, kind) for kind in attachments.TARGETS)


def _may_change_some_file(user):
    return any(attachments.can_change(user, kind) for kind in attachments.TARGETS)


def _back(target_kind, target):
    """Back to the page the file was attached from."""
    if target_kind == "accident":
        return reverse("web:vehicle_accident", args=[target.pk])
    name = {"fuel": "vehicle_fuel", "service": "vehicle_service", "fine": "vehicle_incidents", "loan": "vehicle_loan"}[target_kind]
    url = reverse(f"web:{name}", args=[target.vehicle_id])
    return f"{url}?loan={target.pk}" if target_kind == "loan" else url


@login_required
def vehicle_file_add(request, target_kind, pk):
    spec = attachments.TARGETS.get(target_kind)
    if spec is None:
        raise Http404
    if not attachments.can_change(request.user, target_kind):
        raise PermissionDenied
    target = get_object_or_404(spec["model"].objects.for_user(request.user).select_related("vehicle"), pk=pk)
    back = _back(target_kind, target)
    if getattr(target, "is_voided", False):
        messages.error(request, "A cancelled entry cannot take files.")
        return redirect(back)
    form = AttachmentForm(request.POST or None, request.FILES or None, allowed=spec["allowed"])
    if request.method == "POST" and form.is_valid():
        cd = form.cleaned_data
        try:
            attachments.attach(target_kind, target, kind=cd["kind"], file=cd["file"], title=cd["title"])
        except VehicleError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "File attached.")
            return redirect(back)
    return _form_page(request, form, f"Attach a file: {target.vehicle.plate_number}", back, "file",
                      intro="Scans and photos are kept private. Every view and download is recorded in the audit log.")


@login_required
def vehicle_file(request, pk):
    """Open (or, with ?download=1, download) a file. Checked against the kind of entry it belongs to, and audited."""
    if not _may_see_some_file(request.user):                  # as your other pages: no permission at all is a 403
        raise PermissionDenied
    attached = get_object_or_404(VehicleFile.objects.for_user(request.user).filter(is_removed=False), pk=pk)
    if not attachments.can_view(request.user, attached.target_kind):
        raise PermissionDenied
    try:
        handle = attached.file.open("rb")
    except FileNotFoundError:
        raise Http404
    audit.log("download", attached, module="vehicles", company_id=attached.company_id)
    name = slugify(f"{attached.vehicle.plate_number}-{attached.label}") or "file"
    response = FileResponse(handle, as_attachment=request.GET.get("download") == "1",
                            filename=name + Path(attached.file.name).suffix.lower())
    response["X-Content-Type-Options"] = "nosniff"
    return response


@login_required
def vehicle_file_remove(request, pk):
    if not _may_change_some_file(request.user):
        raise PermissionDenied
    attached = get_object_or_404(VehicleFile.objects.for_user(request.user).filter(is_removed=False)
                                 .select_related("vehicle"), pk=pk)
    kind = attached.target_kind
    if not attachments.can_change(request.user, kind):
        raise PermissionDenied
    back = _back(kind, attached.target)
    form = VoidForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            attachments.remove(attached, form.cleaned_data["reason"])
        except VehicleError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "The file was removed.")
            return redirect(back)
    return _form_page(request, form, f"Remove file: {attached.label}", back, "void",
                      intro="The file is hidden from the entry but kept, with this reason, in the audit trail.")


@hr_perm("vehicles.view_vehicle")
def vehicle_files(request, pk):
    """Every file on one vehicle that the user may see, whatever it is attached to."""
    vehicle = _vehicle(request, pk)
    files = (vehicle.files.filter(is_removed=False)
             .select_related("fuel_fill", "service_record", "fine", "accident", "loan"))
    visible = [f for f in files if attachments.can_view(request.user, f.target_kind)]
    return render(request, "web/vehicles/files.html", {"vehicle": vehicle, "files": visible,
                                                       "hidden": len(files) - len(visible)})
