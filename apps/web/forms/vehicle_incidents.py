from decimal import Decimal

from django import forms
from django.utils import timezone

from apps.employees.models import Employee
from apps.vehicles.incidents import Accident, Fine
from apps.vehicles.upkeep import ServiceRecord

from apps.web.forms.common import date_input


def _driver_field(field, user, vehicle):
    """Anyone of the vehicle's company, including people who have left: fines and claims arrive late."""
    field.queryset = (Employee.objects.for_user(user).filter(company_id=vehicle.company_id)
                      .order_by("employee_no", "first_name"))
    field.label_from_instance = lambda e: (f"{e.employee_no} - {e.full_name}" if e.employee_no else e.full_name) + (
        " (left)" if e.status == Employee.Status.SEPARATED else "")
    field.required = False
    field.empty_label = "Whoever had the vehicle that day"
    field.help_text = "Leave it as it is to use the hand-over history. Choose someone only to override it."


class FineForm(forms.ModelForm):
    class Meta:
        model = Fine
        fields = ["fined_on", "reference", "offence", "location", "amount", "driver", "charged_to_employee", "notes"]
        widgets = {"fined_on": date_input(max="today"), "notes": forms.Textarea(attrs={"rows": 2})}
        labels = {"charged_to_employee": "Charge this fine to the driver", "driver": "Driver at the time"}
        help_texts = {
            "reference": "The number on the ticket. The same number cannot be entered twice for one vehicle.",
            "charged_to_employee": "Payroll will deduct it once payroll exists. Tick it only if the driver pays."}

    def __init__(self, *args, user, vehicle, **kwargs):
        super().__init__(*args, **kwargs)
        self.vehicle = vehicle
        self.instance.vehicle = vehicle
        _driver_field(self.fields["driver"], user, vehicle)
        if not self.instance.pk:
            self.initial.setdefault("fined_on", timezone.localdate())


class PayFineForm(forms.Form):
    paid_on = forms.DateField(widget=date_input(max="today"), label="Paid on")
    amount = forms.DecimalField(min_value=Decimal("0.001"), max_digits=12, decimal_places=3,
                                label="Amount paid (BHD)")
    reference = forms.CharField(required=False, max_length=60, label="Payment reference")

    def __init__(self, *args, fine, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["paid_on"].widget.attrs["data-min"] = fine.fined_on.isoformat()
        self.initial.setdefault("paid_on", timezone.localdate())
        self.initial.setdefault("amount", fine.amount)


class AccidentForm(forms.ModelForm):
    class Meta:
        model = Accident
        fields = ["occurred_on", "location", "description", "driver", "police_report_no", "fault", "injuries",
                  "third_party", "third_party_details", "claim_status", "claim_no", "insurance_recovered", "notes"]
        widgets = {"occurred_on": date_input(max="today"), "description": forms.Textarea(attrs={"rows": 3}),
                   "third_party_details": forms.Textarea(attrs={"rows": 2}), "notes": forms.Textarea(attrs={"rows": 2})}
        labels = {"driver": "Driver at the time"}

    def __init__(self, *args, user, vehicle, **kwargs):
        super().__init__(*args, **kwargs)
        self.vehicle = vehicle
        self.instance.vehicle = vehicle
        _driver_field(self.fields["driver"], user, vehicle)
        if not self.instance.pk:
            self.initial.setdefault("occurred_on", timezone.localdate())


class CloseAccidentForm(forms.Form):
    closed_on = forms.DateField(widget=date_input(max="today"), label="Closed on")

    def __init__(self, *args, accident, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["closed_on"].widget.attrs["data-min"] = accident.occurred_on.isoformat()
        self.initial.setdefault("closed_on", timezone.localdate())


class LinkRepairForm(forms.Form):
    repair = forms.ModelChoiceField(queryset=ServiceRecord.objects.none(), empty_label="Choose a repair",
                                    label="Repair logged in Maintenance")

    def __init__(self, *args, accident, **kwargs):
        super().__init__(*args, **kwargs)
        field = self.fields["repair"]
        field.queryset = (ServiceRecord.objects.filter(vehicle_id=accident.vehicle_id, kind=ServiceRecord.Kind.REPAIR,
                                                       is_voided=False, accidents__isnull=True)
                          .order_by("-serviced_on"))
        field.label_from_instance = lambda r: (
            f"{r.serviced_on:%d %b %Y}: {r.garage or 'repair'}, {r.total_cost:.3f} BHD")
        field.help_text = ("Only repairs of this vehicle that are not linked to another accident are listed. "
                           "Log the repair under Maintenance first.")
