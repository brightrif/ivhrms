from apps.attendance.models import AttendanceCorrection
from apps.leave.models import LeaveRequest


def describe(approval):
    """Turn whatever an approval points at into something an approver can read."""
    t = approval.target
    if isinstance(t, LeaveRequest):
        return {"kind": "Leave", "title": f"{t.leave_type.name}, {t.days} day(s)",
                "detail": f"{t.start_date:%a %d %b %Y} to {t.end_date:%a %d %b %Y}", "reason": t.reason}
    if isinstance(t, AttendanceCorrection):
        return {"kind": "Attendance", "title": f"Correct to {t.get_requested_status_display()}",
                "detail": f"{t.date:%a %d %b %Y}", "reason": t.reason}
    return {"kind": approval.flow.name, "title": str(t), "detail": "", "reason": ""}


def item(approval):
    return {"approval": approval, "info": describe(approval)}