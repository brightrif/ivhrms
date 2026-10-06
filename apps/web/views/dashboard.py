"""The employee's home page."""

from django.shortcuts import render
from django.utils import timezone

from apps.attendance.models import Attendance
from apps.core import approvals
from apps.leave import services as leave_services
from apps.leave.models import LeaveRequest, LeaveType
from apps.web.access import employee_required
from apps.web.views.leave import decorate_leaves


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
        "leaves": decorate_leaves(LeaveRequest.objects.filter(employee=emp).select_related("leave_type")[:5]),
    })
