from django.contrib import admin

from .models import LeaveLedger, LeaveRequest, LeaveType


@admin.register(LeaveType)
class LeaveTypeAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "company", "is_paid", "tracks_balance", "annual_entitlement")
    list_filter = ("company",)


@admin.register(LeaveRequest)
class LeaveRequestAdmin(admin.ModelAdmin):
    """Read-only: requests are created through leave.services.apply so every rule is enforced."""
    list_display = ("employee", "leave_type", "start_date", "end_date", "days", "status")
    list_filter = ("company", "status", "leave_type")
    search_fields = ("employee__employee_no", "employee__first_name")
    date_hierarchy = "start_date"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(LeaveLedger)
class LeaveLedgerAdmin(admin.ModelAdmin):
    """Add-only: HR enters adjustments and compensatory leave here. Rows are never edited."""
    fields = ("employee", "leave_type", "year", "kind", "days", "remarks")
    list_display = ("employee", "leave_type", "year", "kind", "days", "remarks")
    list_filter = ("company", "leave_type", "year", "kind")
    search_fields = ("employee__employee_no", "employee__first_name")

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False