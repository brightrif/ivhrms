"""Self-service leave: apply, list, cancel and the live day-count preview."""

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.attendance import services as att_services
from apps.leave import services as leave_services
from apps.leave.models import LeaveRequest
from apps.web.access import employee_required
from apps.web.forms.leave import LeaveApplyForm


def decorate_leaves(leaves):
    today = timezone.localdate()
    rows = list(leaves)
    for r in rows:
        r.can_cancel = r.status == "pending" or (r.status == "approved" and r.start_date > today)
    return rows


@employee_required
def leave_list(request):
    qs = LeaveRequest.objects.filter(employee=request.employee).select_related("leave_type")[:50]
    return render(request, "web/leave/list.html", {"leaves": decorate_leaves(qs)})


@employee_required
def leave_apply(request):
    emp = request.employee
    if request.method == "POST":
        form = LeaveApplyForm(request.POST, request.FILES, employee=emp)
        if form.is_valid():
            cd = form.cleaned_data
            try:
                leave_services.apply(emp, cd["leave_type"], cd["start_date"], cd["end_date"],
                                     requested_by=request.user, is_half_day=cd["is_half_day"],
                                     reason=cd["reason"], attachment=cd["attachment"])
            except leave_services.LeaveError as exc:
                form.add_error(None, str(exc))
            else:
                messages.success(request, "Leave request submitted for approval.")
                return redirect("web:leave_list")
    else:
        form = LeaveApplyForm(employee=emp)
    return render(request, "web/leave/apply.html", {"form": form})


@employee_required
@require_POST
def leave_preview(request):
    """HTMX: how many days will this charge, and what is left afterwards?"""
    emp = request.employee
    form = LeaveApplyForm(request.POST, employee=emp)
    if not form.is_valid():
        return render(request, "web/leave/_preview.html",
                      {"hint": "Choose a leave type and dates to see how many days will be charged."})
    cd = form.cleaned_data
    try:
        days = leave_services.compute_days(emp, cd["leave_type"], cd["start_date"], cd["end_date"],
                                           cd["is_half_day"])
    except leave_services.LeaveError as exc:
        return render(request, "web/leave/_preview.html", {"error": str(exc)})
    ctx = {"days": days, "tracked": cd["leave_type"].tracks_balance}
    if ctx["tracked"]:
        avail = leave_services.available(emp, cd["leave_type"], cd["start_date"].year)
        ctx.update(available=avail, after=avail - days, short=days > avail)
    return render(request, "web/leave/_preview.html", ctx)


@employee_required
@require_POST
def leave_cancel(request, pk):
    leave = get_object_or_404(LeaveRequest, pk=pk, employee=request.employee)
    error = ""
    try:
        leave_services.cancel(leave.pk, request.user)
    except (leave_services.LeaveError, att_services.AttendanceError) as exc:
        error = str(exc)
    leave.refresh_from_db()
    return render(request, "web/leave/_row.html", {"l": decorate_leaves([leave])[0], "error": error})
