from django.contrib import admin
from .models import Employee, EmploymentRecord, EmployeeNumberSequence
from . import numbering

class HistoryInline(admin.TabularInline):
    model = EmploymentRecord
    extra = 0
    fk_name = "employee"
    readonly_fields = ("effective_from", "effective_to", "reason", "department", "designation",
                       "grade", "location", "reporting_manager", "employment_type")
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    list_display = ("employee_no", "first_name", "last_name", "worker_type", "department", "status")
    list_filter = ("worker_type", "status", "department")
    search_fields = ("employee_no", "first_name", "last_name", "name_ar", "email")
    inlines = [HistoryInline]

@admin.register(EmployeeNumberSequence)
class EmployeeNumberSequenceAdmin(admin.ModelAdmin):
    """Tune an employee-number series: prefix, digits, and the next number (e.g. start at 1500)."""
    list_display = ("company", "worker_type", "prefix", "padding", "next_number", "next_example")

    def next_example(self, obj):
        return numbering.format_number(obj, obj.next_number)

    def get_readonly_fields(self, request, obj=None):
        return ("company", "worker_type") if obj else ()

    def has_delete_permission(self, request, obj=None):
        return False