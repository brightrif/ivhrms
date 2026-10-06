from django import forms
from django.utils import timezone

from apps.employees.models import Employee

from apps.web.forms.common import date_input


def _employee_label(e):
    return f"{e.employee_no} - {e.full_name}" if e.employee_no else e.full_name


class AssignForm(forms.Form):
    employee = forms.ModelChoiceField(queryset=Employee.objects.none(), empty_label="Choose an employee")
    assigned_from = forms.DateField(widget=date_input(max="today"), label="Handed over on")
    odometer = forms.IntegerField(min_value=0, label="Odometer at hand-over (km)")
    confirm_licence = forms.BooleanField(
        required=False, label="Assign anyway",
        help_text="Only needed if the driving licence is missing or expired and you have checked it elsewhere.")
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, user, vehicle, **kwargs):
        super().__init__(*args, **kwargs)
        field = self.fields["employee"]
        field.queryset = (Employee.objects.for_user(user).filter(company_id=vehicle.company_id)
                          .exclude(status=Employee.Status.SEPARATED).order_by("employee_no"))
        field.label_from_instance = _employee_label
        self.initial.setdefault("assigned_from", timezone.localdate())
        self.initial.setdefault("odometer", vehicle.odometer)


class ReturnForm(forms.Form):
    returned_on = forms.DateField(widget=date_input(max="today"), label="Returned on")
    odometer = forms.IntegerField(min_value=0, label="Odometer at return (km)")
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, assignment, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["returned_on"].widget.attrs["data-min"] = assignment.assigned_from.isoformat()
        self.fields["odometer"].min_value = assignment.start_odometer
        self.fields["odometer"].widget.attrs["min"] = assignment.start_odometer
        self.initial.setdefault("returned_on", timezone.localdate())
        self.initial.setdefault("odometer", max(assignment.vehicle.odometer, assignment.start_odometer))


class OdometerForm(forms.Form):
    reading_on = forms.DateField(widget=date_input(max="today"), label="Reading taken on")
    odometer = forms.IntegerField(min_value=0, label="Odometer (km)")
    note = forms.CharField(required=False, max_length=200)

    def __init__(self, *args, vehicle, **kwargs):
        super().__init__(*args, **kwargs)
        self.initial.setdefault("reading_on", timezone.localdate())
        self.initial.setdefault("odometer", vehicle.odometer)
        self.fields["odometer"].help_text = f"It cannot be lower than the earlier readings (now {vehicle.odometer} km)."
