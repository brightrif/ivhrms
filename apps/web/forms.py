from django import forms

from apps.attendance.models import Attendance
from apps.leave.models import LeaveType
from .calendar_hints import leave_calendar_hints      # at the top of the file

def date_input(**rules):
    """A date field. Optional rules reach the date picker as data-* attributes (see static/web/datepickers.js)."""
    attrs = {"type": "date"}
    attrs.update({"data-" + key.replace("_", "-"): value for key, value in rules.items()})
    return forms.DateInput(format="%Y-%m-%d", attrs=attrs)


def time_input():
    return forms.TimeInput(format="%H:%M", attrs={"type": "time"})


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


_S = Attendance.Status
CORRECTION_STATUSES = [(_S.PRESENT.value, "Present"), (_S.HALF_DAY.value, "Half day"), (_S.ABSENT.value, "Absent")]


class CorrectionForm(forms.Form):
    status = forms.ChoiceField(choices=CORRECTION_STATUSES, label="Should be")
    check_in = forms.TimeField(required=False, widget=time_input())
    check_out = forms.TimeField(required=False, widget=time_input())
    reason = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}))

    def clean(self):
        data = super().clean()
        if data.get("status") == _S.ABSENT.value:
            data["check_in"] = data["check_out"] = None
        return data