from django import forms
from django.contrib import admin
from django.core.exceptions import ValidationError

from . import services
from .models import Holiday, Shift, ShiftAssignment


@admin.register(Holiday)
class HolidayAdmin(admin.ModelAdmin):
    list_display = ("date", "name", "company", "is_active")
    list_filter = ("company", "is_active")
    date_hierarchy = "date"


@admin.register(Shift)
class ShiftAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "company", "kind", "start_time", "end_time", "grace_minutes")
    list_filter = ("company", "kind")


class ShiftAssignmentForm(forms.ModelForm):
    class Meta:
        model = ShiftAssignment
        fields = ["employee", "shift", "effective_from"]

    def clean(self):
        data = super().clean()
        if all(k in data for k in ("employee", "shift", "effective_from")):
            try:
                services.check_assignable(data["employee"], data["shift"], data["effective_from"])
            except services.SchedulingError as exc:
                raise ValidationError(str(exc))
        return data


@admin.register(ShiftAssignment)
class ShiftAssignmentAdmin(admin.ModelAdmin):
    form = ShiftAssignmentForm
    list_display = ("employee", "shift", "effective_from", "effective_to")
    list_filter = ("company", "shift")

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        created = services.assign_shift(obj.employee, obj.shift, obj.effective_from)   # closes the previous one
        obj.pk, obj.company_id, obj._state.adding = created.pk, created.company_id, False