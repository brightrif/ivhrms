from django import forms
from django.utils import timezone

from apps.employees.models import Employee
from apps.vehicles.custody import CustodyRequest

from apps.web.forms.common import date_input


class CustodyForm(forms.Form):
    decision = forms.ChoiceField(choices=CustodyRequest.Decision.choices, widget=forms.RadioSelect,
                                 label="What should happen to the vehicle?")
    new_driver = forms.ModelChoiceField(queryset=Employee.objects.none(), required=False, empty_label="Not needed",
                                        label="Hand over to")
    planned_on = forms.DateField(required=False, widget=date_input(), label="When",
                                 help_text="Only a plan, for example the last working day. HR records the real date "
                                           "when the hand-over or return happens.")
    reason = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}), label="Reason",
                             help_text="Required when the vehicle stays with the employee.")

    def __init__(self, *args, user, vehicle, holder, **kwargs):
        super().__init__(*args, **kwargs)
        field = self.fields["new_driver"]
        field.queryset = (Employee.objects.for_user(user).filter(company_id=vehicle.company_id,
                                                                 status=Employee.Status.ACTIVE)
                          .exclude(pk=holder.pk).order_by("employee_no"))
        field.label_from_instance = lambda e: f"{e.employee_no} - {e.full_name}" if e.employee_no else e.full_name


class RecoverForm(forms.Form):
    recovered_on = forms.DateField(widget=date_input(max="today"), label="Recovered on")
    note = forms.CharField(required=False, max_length=200, label="Note",
                           help_text="For example which month's salary it was taken from.")

    def __init__(self, *args, fine, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["recovered_on"].widget.attrs["data-min"] = fine.fined_on.isoformat()
        self.initial.setdefault("recovered_on", timezone.localdate())
