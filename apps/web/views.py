from calendar import monthrange
from collections import Counter
from datetime import date, datetime, timedelta
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from apps.attendance import services as att_services
from apps.attendance.models import Attendance, AttendanceCorrection
from apps.core import approvals
from apps.core.models import ApprovalRequest
from apps.leave import services as leave_services
from apps.leave.models import LeaveRequest, LeaveType
from apps.scheduling.services import day_types, shift_on

from django.contrib.auth import views as auth_views
from django.urls import reverse_lazy

from . import presenters
from .forms import CorrectionForm, LeaveApplyForm

SERVICE_ERRORS = (approvals.ApprovalError, leave_services.LeaveError, att_services.AttendanceError)


def employee_required(view):
    @login_required
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        employee = getattr(request.user, "employee", None)
        if employee is None:
            return render(request, "web/no_employee.html", status=403)
        request.employee = employee
        return view(request, *args, **kwargs)
    return wrapper


# ------------------------------------------------------------------ dashboard

@employee_required
def dashboard(request):
    emp, today = request.employee, timezone.localdate()
    balances = []
    for lt in LeaveType.objects.filter(company=emp.company, is_active=True, tracks_balance=True):
        balances.append({"type": lt,
                         "balance": leave_services.balance(emp, lt, today.year),
                         "available": leave_services.available(emp, lt, today.year)})
    return render(request, "web/dashboard.html", {
        "employee": emp, "year": today.year, "balances": balances,
        "today_rec": Attendance.objects.filter(employee=emp, date=today).first(),
        "pending_count": len(approvals.pending_for(request.user)),
        "leaves": _decorate_leaves(LeaveRequest.objects.filter(employee=emp).select_related("leave_type")[:5]),
    })


# ------------------------------------------------------------------ leave

def _decorate_leaves(leaves):
    today = timezone.localdate()
    rows = list(leaves)
    for r in rows:
        r.can_cancel = r.status == "pending" or (r.status == "approved" and r.start_date > today)
    return rows


@employee_required
def leave_list(request):
    qs = LeaveRequest.objects.filter(employee=request.employee).select_related("leave_type")[:50]
    return render(request, "web/leave_list.html", {"leaves": _decorate_leaves(qs)})


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
    return render(request, "web/leave_apply.html", {"form": form})


@employee_required
@require_POST
def leave_preview(request):
    """HTMX: how many days will this charge, and what is left afterwards?"""
    emp = request.employee
    form = LeaveApplyForm(request.POST, employee=emp)
    if not form.is_valid():
        return render(request, "web/_leave_preview.html",
                      {"hint": "Choose a leave type and dates to see how many days will be charged."})
    cd = form.cleaned_data
    try:
        days = leave_services.compute_days(emp, cd["leave_type"], cd["start_date"], cd["end_date"],
                                           cd["is_half_day"])
    except leave_services.LeaveError as exc:
        return render(request, "web/_leave_preview.html", {"error": str(exc)})
    ctx = {"days": days, "tracked": cd["leave_type"].tracks_balance}
    if ctx["tracked"]:
        avail = leave_services.available(emp, cd["leave_type"], cd["start_date"].year)
        ctx.update(available=avail, after=avail - days, short=days > avail)
    return render(request, "web/_leave_preview.html", ctx)


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
    return render(request, "web/_leave_row.html", {"l": _decorate_leaves([leave])[0], "error": error})


# ------------------------------------------------------------------ approvals

def _approval(pk):
    return get_object_or_404(
        ApprovalRequest.objects.select_related("flow", "employee__reporting_manager"), pk=pk)


@login_required
def approvals_inbox(request):
    items = [presenters.item(a) for a in approvals.pending_for(request.user)]
    return render(request, "web/approvals.html", {"items": items})


@login_required
def approval_count(request):
    n = len(approvals.pending_for(request.user))
    return HttpResponse(f'<span class="badge text-bg-danger rounded-pill">{n}</span>' if n else "")


@login_required
def approval_row(request, pk):
    a = _approval(pk)
    if not approvals.can_act(a, request.user):
        return render(request, "web/_approval_done.html", {"message": "This request is no longer waiting on you."})
    return render(request, "web/_approval_row.html", {"item": presenters.item(a)})


@login_required
def approval_reject_form(request, pk):
    a = _approval(pk)
    if not approvals.can_act(a, request.user):
        raise Http404
    return render(request, "web/_approval_reject_form.html", {"a": a})


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
        return render(request, "web/_approval_row.html", {"item": presenters.item(a), "error": str(exc)})

    message = ("Rejected." if result.status == "rejected"
               else "Approved. The request is complete." if result.status == "approved"
               else "Approved. Forwarded to the next approver.")
    resp = render(request, "web/_approval_done.html", {"message": message, "item": presenters.item(result)})
    resp["HX-Trigger"] = "approvalsChanged"        # refreshes the nav badge
    return resp


# ------------------------------------------------------------------ attendance

def _parse_month(value, today):
    try:
        year, month = (int(x) for x in value.split("-"))
        return date(year, month, 1)
    except (ValueError, AttributeError):
        return today.replace(day=1)


def _parse_day(value):
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise Http404


def _day_rows(emp, first, last):
    today = timezone.localdate()
    records = {a.date: a for a in Attendance.objects.filter(employee=emp, date__range=(first, last))}
    kinds = day_types(emp, first, last)
    pending = set(AttendanceCorrection.objects
                  .filter(employee=emp, date__range=(first, last), status=AttendanceCorrection.Status.PENDING)
                  .values_list("date", flat=True))
    rows, d = [], first
    while d <= last:
        rec = records.get(d)
        rows.append({"date": d, "rec": rec, "kind": kinds[d], "future": d > today, "pending": d in pending,
                     "can_correct": d <= today and d not in pending and not (rec and rec.is_locked)})
        d += timedelta(days=1)
    return rows


@employee_required
def attendance(request):
    emp, today = request.employee, timezone.localdate()
    first = _parse_month(request.GET.get("month"), today)
    last = first.replace(day=monthrange(first.year, first.month)[1])
    rows = _day_rows(emp, first, last)
    counts = Counter(r["rec"].status for r in rows if r["rec"])
    summary = {"worked": counts["present"] + counts["late_entry"] + counts["early_exit"],
               "half": counts["half_day"], "absent": counts["absent"],
               "leave": counts["paid_leave"] + counts["unpaid_leave"], "late": counts["late_entry"]}
    nxt = last + timedelta(days=1)
    return render(request, "web/attendance.html", {
        "rows": rows, "summary": summary, "month": first,
        "prev_month": (first - timedelta(days=1)).strftime("%Y-%m"),
        "next_month": nxt.strftime("%Y-%m") if nxt <= today else None,
    })


@employee_required
def attendance_row(request, day):
    d = _parse_day(day)
    return render(request, "web/_att_row.html", {"r": _day_rows(request.employee, d, d)[0]})


@employee_required
@require_http_methods(["GET", "POST"])
def attendance_correct(request, day):
    emp, d = request.employee, _parse_day(day)
    ctx = {"iso": d.isoformat()}
    if request.method == "GET":
        return render(request, "web/_att_correction_form.html", {**ctx, "form": CorrectionForm()})

    form = CorrectionForm(request.POST)
    if form.is_valid():
        cd = form.cleaned_data
        if d < emp.joining_date:
            form.add_error(None, "That date is before your joining date.")
        else:
            check_in = timezone.make_aware(datetime.combine(d, cd["check_in"])) if cd["check_in"] else None
            check_out = None
            if cd["check_out"]:
                out_date, shift = d, shift_on(emp, d)
                if shift and shift.crosses_midnight and cd["check_out"] <= (cd["check_in"] or shift.start_time):
                    out_date = d + timedelta(days=1)             # night shift ends the next morning
                check_out = timezone.make_aware(datetime.combine(out_date, cd["check_out"]))
            try:
                att_services.request_correction(emp, d, cd["status"], requested_by=request.user,
                                                reason=cd["reason"], check_in=check_in, check_out=check_out)
            except att_services.AttendanceError as exc:
                form.add_error(None, str(exc))
            else:
                resp = render(request, "web/_att_row.html", {"r": _day_rows(emp, d, d)[0]})
                resp["HX-Retarget"] = f"#att-row-{d.isoformat()}"     # replace the whole row, not the form
                resp["HX-Reswap"] = "outerHTML"
                return resp
    return render(request, "web/_att_correction_form.html", {**ctx, "form": form})


class PasswordChangeView(auth_views.PasswordChangeView):
    template_name = "web/password_change.html"
    success_url = reverse_lazy("web:dashboard")

    def form_valid(self, form):
        response = super().form_valid(form)
        user = self.request.user
        if user.must_change_password:
            user.must_change_password = False
            user.save(update_fields=["must_change_password"])
        return response