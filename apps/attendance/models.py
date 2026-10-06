from django.db import models

from apps.audit.registry import audited
from apps.core.models import BaseModel


@audited(module="attendance", subject="employee_id", company="company_id")
class Attendance(BaseModel):
    class Status(models.TextChoices):
        PRESENT = "present"
        ABSENT = "absent"
        HALF_DAY = "half_day"
        PAID_LEAVE = "paid_leave"
        UNPAID_LEAVE = "unpaid_leave"
        HOLIDAY = "holiday"
        WEEKLY_OFF = "weekly_off"
        LATE_ENTRY = "late_entry"
        EARLY_EXIT = "early_exit"

    class Source(models.TextChoices):
        MANUAL = "manual"
        BULK = "bulk"
        LEAVE = "leave"
        SYSTEM = "system"
        CORRECTION = "correction"

    # Statuses where the person actually worked (late/early are *worked* days, just flagged)
    WORKED = {Status.PRESENT, Status.LATE_ENTRY, Status.EARLY_EXIT, Status.HALF_DAY}
    # Payroll will read this: 1 = full paid day, 0.5 = half, 0 = unpaid
    DAY_FRACTION = {Status.PRESENT: 1, Status.LATE_ENTRY: 1, Status.EARLY_EXIT: 1, Status.PAID_LEAVE: 1,
                    Status.HOLIDAY: 1, Status.WEEKLY_OFF: 1, Status.HALF_DAY: 0.5,
                    Status.ABSENT: 0, Status.UNPAID_LEAVE: 0}

    company = models.ForeignKey("organization.Company", on_delete=models.PROTECT, related_name="+", editable=False)
    employee = models.ForeignKey("employees.Employee", on_delete=models.PROTECT, related_name="attendance_records")
    date = models.DateField()
    status = models.CharField(max_length=15, choices=Status.choices)
    shift = models.ForeignKey("scheduling.Shift", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    check_in = models.DateTimeField(null=True, blank=True)
    check_out = models.DateTimeField(null=True, blank=True)
    late_minutes = models.PositiveIntegerField(default=0)
    early_exit_minutes = models.PositiveIntegerField(default=0)

    # Snapshots taken when the entry is made, so department/site reports stay correct after transfers
    department = models.ForeignKey("organization.Department", null=True, blank=True, on_delete=models.PROTECT,
                                   related_name="+", editable=False)
    location = models.ForeignKey("organization.Location", null=True, blank=True, on_delete=models.PROTECT,
                                 related_name="+")
    project = models.ForeignKey("organization.Project", null=True, blank=True, on_delete=models.PROTECT,
                                related_name="+")

    leave_request = models.ForeignKey("leave.LeaveRequest", null=True, blank=True, on_delete=models.SET_NULL,
                                      related_name="attendance_records")
    source = models.CharField(max_length=12, choices=Source.choices, default=Source.MANUAL)
    remarks = models.CharField(max_length=255, blank=True)
    is_locked = models.BooleanField(default=False, help_text="Set when the payroll period is closed")

    class Meta:
        ordering = ["-date", "employee_id"]
        constraints = [models.UniqueConstraint(fields=["employee", "date"], name="uniq_attendance_employee_date")]
        indexes = [models.Index(fields=["company", "date"]), models.Index(fields=["project", "date"]),
                   models.Index(fields=["location", "date"]), models.Index(fields=["department", "date"])]

    @property
    def day_fraction(self):
        return self.DAY_FRACTION[self.status]

    def save(self, *args, **kwargs):
        self.company_id = self.employee.company_id
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.employee.employee_no} {self.date} {self.status}"


@audited(module="attendance", subject="employee_id", company="company_id")
class AttendanceCorrection(BaseModel):
    class Status(models.TextChoices):
        PENDING = "pending"
        APPROVED = "approved"
        REJECTED = "rejected"
        CANCELLED = "cancelled"

    company = models.ForeignKey("organization.Company", on_delete=models.PROTECT, related_name="+", editable=False)
    employee = models.ForeignKey("employees.Employee", on_delete=models.PROTECT, related_name="attendance_corrections")
    date = models.DateField()
    requested_status = models.CharField(max_length=15, choices=Attendance.Status.choices)
    check_in = models.DateTimeField(null=True, blank=True)
    check_out = models.DateTimeField(null=True, blank=True)
    project = models.ForeignKey("organization.Project", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    location = models.ForeignKey("organization.Location", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    reason = models.TextField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)

    class Meta:
        ordering = ["-date"]

    def save(self, *args, **kwargs):
        self.company_id = self.employee.company_id
        super().save(*args, **kwargs)