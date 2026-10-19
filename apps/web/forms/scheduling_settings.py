from django import forms

from apps.organization.services import companies_for
from apps.scheduling.calendar_rules import WEEKDAYS

from apps.web.forms.common import date_input

# Sunday first, like the attendance sheets; the numbers are Python's weekdays (Monday=0 ... Sunday=6)
DAY_CHOICES = [(i, WEEKDAYS[i]) for i in (6, 0, 1, 2, 3, 4, 5)]


class HolidayForm(forms.Form):
    first_date = forms.DateField(label="First day", widget=date_input())
    last_date = forms.DateField(label="Last day", required=False, widget=date_input(),
                                help_text="Leave empty for a one-day holiday. A holiday can last up to 31 days.")
    name = forms.CharField(max_length=120, label="Name", help_text="For example: Eid Al Fitr.")
    name_ar = forms.CharField(max_length=120, required=False, label="Name (Arabic)")
    applies_to = forms.ChoiceField(label="Applies to")

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        choices = [(str(c.pk), c.name) for c in companies_for(user)]
        if user.is_superuser:
            choices.insert(0, ("all", "Every company"))
        self.fields["applies_to"].choices = choices


class HolidayEditForm(forms.Form):
    name = forms.CharField(max_length=120, label="Name")
    name_ar = forms.CharField(max_length=120, required=False, label="Name (Arabic)")


class WeeklyOffForm(forms.Form):
    company = forms.ChoiceField(label="Company")
    days = forms.TypedMultipleChoiceField(choices=DAY_CHOICES, coerce=int, widget=forms.CheckboxSelectMultiple(
        attrs={"class": "form-check-input"}), label="Weekly off days")
    effective_from = forms.DateField(label="Starts on", widget=date_input(),
                                     help_text="Earlier days keep the weekly off they had.")

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.companies = {str(c.pk): c for c in companies_for(user)}
        self.fields["company"].choices = [(pk, c.name) for pk, c in self.companies.items()]
