"""Leave application form."""

from django import forms

from apps.leave.models import LeaveType
from apps.web.calendar_hints import leave_calendar_hints
from apps.web.forms.common import date_input


class LeaveApplyForm(forms.Form):
    leave_type = forms.ModelChoiceField(queryset=LeaveType.objects.none())
    start_date = forms.DateField(widget=date_input())
    end_date = forms.DateField(widget=date_input())
    is_half_day = forms.BooleanField(required=False, label="Half day")
    reason = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))
    attachment = forms.FileField(required=False, label="Supporting document")

    def __init__(self, *args, employee, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["leave_type"].queryset = LeaveType.objects.filter(company=employee.company, is_active=True)
        # one range box for both dates, with weekly offs and public holidays shown on the calendar
        self.fields["start_date"].widget.attrs.update(
            {"data-range-end": "#id_end_date", "data-range-label": "Leave dates", **leave_calendar_hints(employee)})

    def clean(self):
        data = super().clean()
        s, e = data.get("start_date"), data.get("end_date")
        if s and e and e < s:
            raise forms.ValidationError("End date cannot be before the start date.")
        return data
