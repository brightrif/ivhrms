from django import forms
from django.utils import timezone

from apps.labor.overtime import OvertimePolicy
from apps.organization.models import Company
from apps.organization.services import companies_for

from apps.web.forms.common import date_input


class OvertimePolicyForm(forms.ModelForm):
    class Meta:
        model = OvertimePolicy
        fields = ["company", "effective_from", "overtime_applies", "working_day_multiplier", "weekly_off_multiplier", "holiday_multiplier",
                  "all_hours_on_days_off", "monthly_divisor"]
        widgets = {"effective_from": date_input()}
        labels = {"effective_from": "Starts on"}

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        companies = companies_for(user)
        company = self.fields["company"]
        company.queryset = companies
        ids = list(companies.values_list("pk", flat=True)[:2])
        if len(ids) == 1:
            self.initial.setdefault("company", ids[0])
            company.widget = forms.HiddenInput()
        self.initial.setdefault("effective_from", timezone.localdate())
