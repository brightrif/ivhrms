from django.contrib import admin

# Register your models here.
from django.contrib import admin
from .models import AuditEvent


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    list_display = ("timestamp", "actor_repr", "action", "module", "object_repr", "channel")
    list_filter = ("action", "module", "channel")
    search_fields = ("actor_repr", "object_repr", "object_id")
    date_hierarchy = "timestamp"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False