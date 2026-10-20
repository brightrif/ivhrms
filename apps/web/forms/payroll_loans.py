import calendar
from datetime import date
from decimal import Decimal

from django import forms
from django.urls import reverse

from apps.employees.models import Employee
from apps.payroll.loans import Loan, LoanPayment

from .common import date_input
from .widgets import EmployeeSearchWidget

MONTHS = [(i, calendar.month_name[i]) for i in range(1, 13)]
MONEY = dict(min_value=Decimal("0.001"), max_digits=12, decimal_places=3)
HOW = [("approval", "Send for approval"), ("verbal", "Already agreed verbally")]
METHODS = [(LoanPayment.Method.BANK, "Bank transfer"), (LoanPayment.Method.CASH, "Cash")]


def _employee_label(e):
    return f"{e.employee_no} - {e.full_name}" if e.employee_no else e.full_name


def _first_month(form, cleaned):
    """The month and year boxes give back the first day of that month, like the payroll run form."""
    if cleaned.get("month") and cleaned.get("year"):
        cleaned["first_month"] = date(cleaned["year"], cleaned["month"], 1)
    return cleaned


class LoanForm(forms.Form):
    employee = forms.ModelChoiceField(queryset=Employee.objects.none(), label="Employee")
    kind = forms.ChoiceField(choices=Loan.Kind.choices, label="Kind")
    amount = forms.DecimalField(label="Amount (BHD)", **MONEY)
    months = forms.IntegerField(min_value=1, max_value=120, initial=12, label="Instalments (months)",
                                help_text="A salary advance is one instalment.")
    interest_rate = forms.DecimalField(min_value=Decimal("0"), max_value=Decimal("100"), max_digits=5, decimal_places=2,
                                       required=False, label="Yearly interest %", help_text="0 means no interest.")
    month = forms.TypedChoiceField(choices=MONTHS, coerce=int, label="First deduction month")
    year = forms.IntegerField(min_value=2000, max_value=2100, label="Year")
    reason = forms.CharField(max_length=255, required=False, label="Reason")
    how = forms.ChoiceField(choices=HOW, initial="approval", label="How was it agreed?",
                            help_text="Management decides a loan sent for approval, as for a final settlement.")
    agreed_by = forms.CharField(max_length=120, required=False, label="Agreed by")
    agreed_on = forms.DateField(required=False, label="Agreed on", widget=date_input(max="today"))
    override_reason = forms.CharField(max_length=255, required=False, label="Why go over the limit?")

    def __init__(self, *args, user, can_verbal=False, editing=False, ask_reason=False, **kwargs):
        super().__init__(*args, **kwargs)
        if editing:
            del self.fields["employee"]
        else:
            field = self.fields["employee"]              # a search box, not a list of everyone
            field.widget = EmployeeSearchWidget(reverse("web:payroll_loan_employee_search"),
                                                "Search by name or number", empty="Nobody matches that")
            field.queryset = Employee.objects.for_user(user).exclude(status=Employee.Status.SEPARATED)
            field.label_from_instance = _employee_label
            field.widget.typed = self.data.get("employee_text", "") if self.is_bound else ""
        if editing or not can_verbal:
            del self.fields["how"], self.fields["agreed_by"], self.fields["agreed_on"]
        if not ask_reason:                                # only shown once a limit has been passed
            del self.fields["override_reason"]

    @property
    def verbal(self):
        return "how" in self.fields and self.cleaned_data.get("how") == "verbal"

    def clean(self):
        cd = _first_month(self, super().clean())
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
    amount = forms.DecimalField(label="Amount (BHD)", **MONEY)
    paid_on = forms.DateField(label="Paid on", widget=date_input(max="today"))
    method = forms.ChoiceField(choices=METHODS, label="How")
    reference = forms.CharField(max_length=80, required=False, label="Reference")


class PrecloseForm(forms.Form):
    paid_on = forms.DateField(label="Paid on", widget=date_input(max="today"))
    method = forms.ChoiceField(choices=METHODS, label="How")
    reference = forms.CharField(max_length=80, required=False, label="Reference")


class RescheduleForm(forms.Form):
    months = forms.IntegerField(min_value=1, max_value=120, label="Instalments for the rest")
    month = forms.TypedChoiceField(choices=MONTHS, coerce=int, label="First deduction month")
    year = forms.IntegerField(min_value=2000, max_value=2100, label="Year")

    def clean(self):
        return _first_month(self, super().clean())


class ReasonForm(forms.Form):
    reason = forms.CharField(max_length=255, label="Reason", widget=forms.Textarea(attrs={"rows": 2}))


class ConfirmForm(forms.Form):
    """No boxes: only a question and the button."""
