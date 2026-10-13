from decimal import Decimal

from django import forms
from django.utils import timezone

from apps.dependants.visa import FamilyVisaRequest, VisaPayment

from apps.web.forms.common import date_input
from apps.web.forms.compliance import SCAN_ATTRS, SCAN_HELP


class RequestForm(forms.Form):
    kind = forms.ChoiceField(label="What is it for?")
    reason = forms.CharField(required=False, label="Reason", widget=forms.Textarea(attrs={"rows": 3}),
                             help_text="Needed for a cancellation. Helpful for the approvers otherwise.")

    def __init__(self, *args, kinds, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["kind"].choices = [(k.value, k.label) for k in kinds]


class SubmittedForm(forms.Form):
    submitted_on = forms.DateField(widget=date_input(max="today"), label="Submitted on")
    reference = forms.CharField(required=False, max_length=60, label="Authority reference",
                                help_text="The application number from the authority, if you have it.")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.initial.setdefault("submitted_on", timezone.localdate())


class CompleteForm(forms.Form):
    """The result. A new or renewed visa needs its details; a cancellation only the date."""
    number = forms.CharField(required=False, max_length=60, label="Visa number")
    issue_date = forms.DateField(required=False, widget=date_input(max="today"), label="Issue date")
    expiry_date = forms.DateField(required=False, widget=date_input(min_from="id_issue_date"), label="Expiry date")
    completed_on = forms.DateField(widget=date_input(max="today"), label="Date completed")

    def __init__(self, *args, request, **kwargs):
        super().__init__(*args, **kwargs)
        self.kind = request.kind
        self.initial.setdefault("completed_on", timezone.localdate())
        if self.kind == FamilyVisaRequest.Kind.CANCELLATION:
            for name in ("number", "issue_date", "expiry_date"):
                del self.fields[name]
            self.fields["completed_on"].label = "Date cancelled"
        else:
            self.fields["expiry_date"].required = True
            if self.kind == FamilyVisaRequest.Kind.NEW:
                self.fields["number"].required = True
            else:
                self.fields["number"].help_text = "Leave blank to keep the old number."

    def clean(self):
        cd = super().clean()
        issue, expiry = cd.get("issue_date"), cd.get("expiry_date")
        if issue and expiry and expiry < issue:
            self.add_error("expiry_date", "The expiry date cannot be before the issue date.")
        return cd


class PaymentForm(forms.ModelForm):
    class Meta:
        model = VisaPayment
        fields = ["description", "paid_on", "government_fee", "service_fee", "fine", "reference", "receipt",
                  "charged_to_employee", "remarks"]
        widgets = {"paid_on": date_input(max="today"), "receipt": forms.FileInput(attrs=SCAN_ATTRS),
                   "remarks": forms.Textarea(attrs={"rows": 2})}
        labels = {"government_fee": "Government fee (BHD)", "service_fee": "Service / agent fee (BHD)",
                  "fine": "Late fine (BHD)", "reference": "Payment reference",
                  "charged_to_employee": "Charge this cost to the employee", "receipt": "Receipt (scan)"}
        help_texts = {"receipt": SCAN_HELP, "charged_to_employee": "Payroll will deduct it once payroll exists. Tick it only if the "
                                             "employee pays."}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.initial.setdefault("paid_on", timezone.localdate())
        for name in ("government_fee", "service_fee", "fine"):
            self.fields[name].required = False            # an empty box means no such cost

    def _money(self, name):
        return self.cleaned_data.get(name) or Decimal("0")

    def clean_government_fee(self):
        return self._money("government_fee")

    def clean_service_fee(self):
        return self._money("service_fee")

    def clean_fine(self):
        return self._money("fine")
