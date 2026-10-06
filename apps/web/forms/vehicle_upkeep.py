from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.vehicles.upkeep import ServicePlan, ServiceRecord

from apps.web.forms.common import date_input


class FuelForm(forms.Form):
    filled_on = forms.DateField(widget=date_input(max="today"), label="Filled on")
    odometer = forms.IntegerField(min_value=0, label="Odometer (km)")
    litres = forms.DecimalField(min_value=Decimal("0.01"), max_digits=8, decimal_places=2)
    cost = forms.DecimalField(min_value=Decimal("0"), max_digits=12, decimal_places=3, label="Total cost (BHD)")
    full_tank = forms.BooleanField(required=False, initial=True, label="Filled to the top",
                                   help_text="Untick for a part fill. Consumption is worked out between full tanks.")
    station = forms.CharField(required=False, max_length=80)
    notes = forms.CharField(required=False, max_length=200)

    def __init__(self, *args, vehicle, **kwargs):
        super().__init__(*args, **kwargs)
        self.initial.setdefault("filled_on", timezone.localdate())
        self.initial.setdefault("odometer", vehicle.odometer)
        self.fields["odometer"].help_text = f"It cannot be lower than the earlier readings (now {vehicle.odometer} km)."


class VoidForm(forms.Form):
    reason = forms.CharField(max_length=200, label="Why is this entry being cancelled?")


class PlanForm(forms.ModelForm):
    class Meta:
        model = ServicePlan
        fields = ["name", "every_km", "every_months", "warn_km", "warn_days", "baseline_on", "baseline_km",
                  "is_active"]
        widgets = {"baseline_on": date_input(max="today")}
        labels = {"name": "What is done"}
        help_texts = {
            "name": 'e.g. "Oil change" or "Tyre rotation".',
            "every_km": "Fill in a distance, a number of months, or both: whichever comes first makes it due.",
            "baseline_on": "Until a service is logged for this plan, counting starts from here.",
            "is_active": "Untick when the plan no longer applies. Its history is kept."}

    def __init__(self, *args, vehicle, **kwargs):
        super().__init__(*args, **kwargs)
        self.vehicle = vehicle
        self.instance.vehicle = vehicle
        if not self.instance.pk:
            self.initial.setdefault("baseline_on", timezone.localdate())
            self.initial.setdefault("baseline_km", vehicle.odometer)
            self.initial.setdefault("is_active", True)

    def clean_name(self):
        return " ".join(self.cleaned_data["name"].split())

    def clean(self):
        cd = super().clean()
        if not cd.get("every_km") and not cd.get("every_months"):
            raise ValidationError("Set a distance, a number of months, or both.")
        # Checked here because Django skips a conditional unique rule whose condition field is not on the form
        name = cd.get("name")
        if name and cd.get("is_active"):
            clash = ServicePlan.objects.filter(vehicle=self.vehicle, name__iexact=name, is_active=True)
            if self.instance.pk:
                clash = clash.exclude(pk=self.instance.pk)
            if clash.exists():
                self.add_error("name", "This vehicle already has an active plan with this name.")
        return cd


class ServiceForm(forms.Form):
    serviced_on = forms.DateField(widget=date_input(max="today"), label="Done on")
    odometer = forms.IntegerField(min_value=0, label="Odometer (km)")
    kind = forms.ChoiceField(choices=ServiceRecord.Kind.choices, initial=ServiceRecord.Kind.SCHEDULED)
    garage = forms.CharField(required=False, max_length=80)
    description = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}),
                                  label="What was done")
    parts_cost = forms.DecimalField(required=False, min_value=Decimal("0"), max_digits=12, decimal_places=3,
                                    label="Parts (BHD)")
    labour_cost = forms.DecimalField(required=False, min_value=Decimal("0"), max_digits=12, decimal_places=3,
                                     label="Labour (BHD)")
    invoice_no = forms.CharField(required=False, max_length=40, label="Invoice number")
    plans = forms.ModelMultipleChoiceField(
        queryset=ServicePlan.objects.none(), required=False, widget=forms.CheckboxSelectMultiple,
        label="Plans this completes", help_text="Ticking a plan restarts its counter from this service.")

    def __init__(self, *args, vehicle, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["plans"].queryset = vehicle.service_plans.filter(is_active=True)
        self.initial.setdefault("serviced_on", timezone.localdate())
        self.initial.setdefault("odometer", vehicle.odometer)

    def clean_parts_cost(self):
        return self.cleaned_data.get("parts_cost") or Decimal("0")

    def clean_labour_cost(self):
        return self.cleaned_data.get("labour_cost") or Decimal("0")
