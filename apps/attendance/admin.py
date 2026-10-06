from django import forms
from django.contrib import admin
from django.core.exceptions import ValidationError

from . import services
from .models import Attendance, AttendanceCorrection


class AttendanceEntryForm(forms.ModelForm):
    class Meta:
        model = Attendance
        fields = ["employee", "date", "status", "check_in", "check_out", "project", "location", "remarks"]

    def clean(self):
        data = super().clean()
        if all(k in data for k in ("employee", "date")):
            try:
                services.validate_new_entry(data["employee"], data["date"], data.get("check_in"), data.get("check_out"))
            except services.AttendanceError as exc:
                raise ValidationError(str(exc))
        return data


@admin.register(Attendance)
class AttendanceAdmin(admin.ModelAdmin):
    """New entries only. Changing a recorded entry needs an approved correction."""
    form = AttendanceEntryForm
    list_display = ("date", "employee", "status", "late_minutes", "department", "location", "project", "source")
    list_filter = ("company", "status", "source", "department", "location", "project")
    search_fields = ("employee__employee_no", "employee__first_name")
    date_hierarchy = "date"

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        created = services.mark_attendance(obj.employee, obj.date, obj.status, check_in=obj.check_in,
                                           check_out=obj.check_out, project=obj.project,
                                           location=obj.location, remarks=obj.remarks)
        obj.pk, obj.status, obj._state.adding = created.pk, created.status, False


@admin.register(AttendanceCorrection)
class AttendanceCorrectionAdmin(admin.ModelAdmin):
    list_display = ("date", "employee", "requested_status", "status", "reason")
    list_filter = ("company", "status")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False