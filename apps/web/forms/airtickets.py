from decimal import Decimal

from django import forms

from apps.airtickets.models import TicketPolicy, TravelProfile
from apps.airtickets.nationalities import known_names
from apps.employees.models import Employee
from apps.organization.models import Grade
from apps.organization.services import companies_for

from apps.web.forms.common import date_input


class PolicyForm(forms.ModelForm):
    nationalities = forms.MultipleChoiceField(
        required=False, choices=(), widget=forms.SelectMultiple(attrs={"size": 9}),
        help_text="Leave empty for every nationality. Hold Ctrl to choose several.")
    employment_types = forms.MultipleChoiceField(
        required=False, choices=Employee.EmploymentType.choices, widget=forms.CheckboxSelectMultiple,
        label="Contract types", help_text="Leave empty for every contract type.")

    class Meta:
        model = TicketPolicy
        fields = ["company", "name", "nationalities", "min_grade", "employment_types", "interval_months",
                  "first_after_months", "spouse_covered", "max_children", "mode", "allowance_amount", "priority",
                  "is_active"]
        labels = {"min_grade": "Minimum grade", "spouse_covered": "Spouse is covered",
                  "max_children": "Children covered (up to)", "mode": "How it is given"}
        help_texts = {"name": "For example: Expatriate staff, Senior managers.",
                      "min_grade": "Leave empty for every grade. Higher ranks count as above.",
                      "first_after_months": "Months of service before the first ticket.",
                      "interval_months": "The gap between tickets, counted from the last one taken.",
                      "allowance_amount": "Only for the allowance. Paid for the employee and each covered family member."}

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        extra = set(self.instance.nationalities or [])
        names = sorted(set(known_names()) | extra)
        self.fields["nationalities"].choices = [(n, n) for n in names]
        self.fields["min_grade"].queryset = Grade.objects.order_by("-rank", "name")
        self.fields["min_grade"].empty_label = "Any grade"
        if self.instance.pk:
            del self.fields["company"]                      # a policy stays with its company
        else:
            self.fields["company"].queryset = companies_for(user)

    def clean(self):
        cd = super().clean()
        mode, amount = cd.get("mode"), cd.get("allowance_amount")
        if mode == TicketPolicy.Mode.ALLOWANCE and not amount:
            self.add_error("allowance_amount", "Enter the allowance amount.")
        if mode == TicketPolicy.Mode.COMPANY:
            cd["allowance_amount"] = None
        company = self.instance.company if self.instance.pk else cd.get("company")
        name = (cd.get("name") or "").strip()
        if company and name and TicketPolicy.objects.filter(company=company, name__iexact=name).exclude(
                pk=self.instance.pk).exists():
            self.add_error("name", "This company already has a policy with this name.")
        return cd


class TravelProfileForm(forms.ModelForm):
    class Meta:
        model = TravelProfile
        fields = ["home_country", "destination_airport", "notes"]
        widgets = {"notes": forms.Textarea(attrs={"rows": 3})}
        labels = {"home_country": "Home country", "destination_airport": "Airport they fly to"}


class PastTicketForm(forms.Form):
    travel_date = forms.DateField(widget=date_input(max="today"), label="Travel date")
    destination = forms.CharField(required=False, max_length=80, help_text="For example: Cochin (COK).")
    cost = forms.DecimalField(required=False, min_value=Decimal("0"), max_digits=12, decimal_places=3,
                              label="Cost (BHD)", help_text="Optional.")
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))
