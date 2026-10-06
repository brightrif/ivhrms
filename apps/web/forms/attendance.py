"""Attendance correction form."""

from django import forms

from apps.attendance.models import Attendance
from apps.web.forms.common import time_input


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
