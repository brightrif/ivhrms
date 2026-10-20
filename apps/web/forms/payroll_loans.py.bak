from datetime import date

from django import forms
from django.core.exceptions import ValidationError

from apps.payroll.loans import Loan, LoanPayment

from .common import date_input


class MonthField(forms.CharField):
    """A month box (YYYY-MM) that gives back the first day of the month."""
    widget = forms.TextInput(attrs={"type": "month"})

    def to_python(self, value):
        value = (super().to_python(value) or "").strip()
        if not value:
            return None
        try:
            year, month = value.split("-")[:2]
            return date(int(year), int(month), 1)
        except (ValueError, TypeError):
            raise ValidationError("Choose a month.") from None


def month_value(d):
    return d.strftime("%Y-%m") if d else ""


class LoanForm(forms.Form):
    kind = forms.ChoiceField(choices=Loan.Kind.choices, label="Kind")
    amount = forms.DecimalField(max_digits=12, decimal_places=3, min_value=0.001, label="Amount (BHD)")
    months = forms.IntegerField(min_value=1, max_value=120, initial=12, label="Instalments (months)",
                                help_text="A salary advance is one instalment.")
    first_month = MonthField(label="First deduction month")
    interest_rate = forms.DecimalField(max_digits=5, decimal_places=2, min_value=0, max_value=100, required=False,
                                       label="Yearly interest %", help_text="0 means no interest.")
    reason = forms.CharField(max_length=255, required=False, label="Reason")
    how = forms.ChoiceField(
        choices=[("approval", "Send for approval"), ("verbal", "Already agreed verbally")], initial="approval",
        widget=forms.RadioSelect, label="How was it agreed?")
    agreed_by = forms.CharField(max_length=120, required=False, label="Agreed by")
    agreed_on = forms.DateField(required=False, label="Agreed on", widget=date_input(max="today"))
    override_reason = forms.CharField(max_length=255, required=False, label="Why go over the limit?")

    def __init__(self, *args, can_verbal=False, editing=False, **kwargs):
        super().__init__(*args, **kwargs)
        if editing or not can_verbal:
            del self.fields["how"], self.fields["agreed_by"], self.fields["agreed_on"]

    @property
    def verbal(self):
        return "how" in self.fields and self.cleaned_data.get("how") == "verbal"

    def clean(self):
        cd = super().clean()
        if self.verbal:
            if not (cd.get("agreed_by") or "").strip():
                self.add_error("agreed_by", "Say who agreed it.")
            if not cd.get("agreed_on"):
                self.add_error("agreed_on", "Say when it was agreed.")
        return cd


class PayoutForm(forms.Form):
    paid_on = forms.DateField(label="Transferred on", widget=date_input(max="today"))
    reference = forms.CharField(max_length=60, label="Bank transfer reference")


class PaymentForm(forms.Form):
    amount = forms.DecimalField(max_digits=12, decimal_places=3, min_value=0.001, label="Amount (BHD)")
    paid_on = forms.DateField(label="Paid on", widget=date_input(max="today"))
    method = forms.ChoiceField(choices=[(LoanPayment.Method.BANK, "Bank transfer"), (LoanPayment.Method.CASH, "Cash")])
    reference = forms.CharField(max_length=80, required=False, label="Reference")


class PrecloseForm(forms.Form):
    paid_on = forms.DateField(label="Paid on", widget=date_input(max="today"))
    method = forms.ChoiceField(choices=[(LoanPayment.Method.BANK, "Bank transfer"), (LoanPayment.Method.CASH, "Cash")])
    reference = forms.CharField(max_length=80, required=False, label="Reference")


class RescheduleForm(forms.Form):
    months = forms.IntegerField(min_value=1, max_value=120, label="Instalments for the rest")
    first_month = MonthField(label="First deduction month")


class ReasonForm(forms.Form):
    reason = forms.CharField(max_length=255, label="Reason", widget=forms.Textarea(attrs={"rows": 2}))
