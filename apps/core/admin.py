from django.contrib import admin
from .models import ApprovalAction, ApprovalFlow, ApprovalRequest, ApprovalStep, SystemSetting


class StepInline(admin.TabularInline):
    model = ApprovalStep
    extra = 1


@admin.register(ApprovalFlow)
class ApprovalFlowAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "is_active")
    inlines = [StepInline]


class ActionInline(admin.TabularInline):
    model = ApprovalAction
    extra = 0
    can_delete = False
    readonly_fields = ("step", "actor", "decision", "comment", "channel", "acted_at")

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(ApprovalRequest)
class ApprovalRequestAdmin(admin.ModelAdmin):
    list_display = ("id", "flow", "employee", "status", "current_step", "created_at")
    list_filter = ("flow", "status")
    inlines = [ActionInline]


admin.site.register(SystemSetting)