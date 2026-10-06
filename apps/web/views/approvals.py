"""The approvals inbox: approve, reject and the menu badge."""

from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_POST

from apps.attendance import services as att_services
from apps.core import approvals
from apps.core.models import ApprovalRequest
from apps.leave import services as leave_services
from apps.web import presenters


SERVICE_ERRORS = (approvals.ApprovalError, leave_services.LeaveError, att_services.AttendanceError)



def _approval(pk):
    return get_object_or_404(
        ApprovalRequest.objects.select_related("flow", "employee__reporting_manager"), pk=pk)


@login_required
def approvals_inbox(request):
    items = [presenters.item(a) for a in approvals.pending_for(request.user)]
    return render(request, "web/approvals/inbox.html", {"items": items})


@login_required
def approval_count(request):
    n = len(approvals.pending_for(request.user))
    return HttpResponse(f'<span class="badge text-bg-danger rounded-pill">{n}</span>' if n else "")


@login_required
def approval_row(request, pk):
    a = _approval(pk)
    if not approvals.can_act(a, request.user):
        return render(request, "web/approvals/_done.html", {"message": "This request is no longer waiting on you."})
    return render(request, "web/approvals/_row.html", {"item": presenters.item(a)})


@login_required
def approval_reject_form(request, pk):
    a = _approval(pk)
    if not approvals.can_act(a, request.user):
        raise Http404
    return render(request, "web/approvals/_reject_form.html", {"a": a})


@login_required
@require_POST
def approval_decide(request, pk):
    a = _approval(pk)
    decision = request.POST.get("decision")
    if decision not in ("approve", "reject"):
        return HttpResponse(status=400)
    try:
        result = approvals.decide(a.pk, request.user, decision,
                                  comment=request.POST.get("comment", ""), channel="web")
    except SERVICE_ERRORS as exc:
        a.refresh_from_db()
        return render(request, "web/approvals/_row.html", {"item": presenters.item(a), "error": str(exc)})

    message = ("Rejected." if result.status == "rejected"
               else "Approved. The request is complete." if result.status == "approved"
               else "Approved. Forwarded to the next approver.")
    resp = render(request, "web/approvals/_done.html", {"message": message, "item": presenters.item(result)})
    resp["HX-Trigger"] = "approvalsChanged"        # refreshes the nav badge
    return resp
