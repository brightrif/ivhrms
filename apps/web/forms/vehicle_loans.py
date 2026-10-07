from decimal import Decimal

from django import forms
from django.utils import timezone

from apps.web.forms.common import date_input

MONEY = dict(max_digits=12, decimal_places=3, min_value=Decimal("0.001"))


class LoanForm(forms.Form):
    lender = forms.CharField(max_length=100, label="Bank / lender")
    account_no = forms.CharField(required=False, max_length=60, label="Loan / account number")
    financed_amount = forms.DecimalField(label="Amount financed (BHD)", **MONEY)
    down_payment = forms.DecimalField(required=False, min_value=Decimal("0"), max_digits=12, decimal_places=3,
                                      initial=Decimal("0"), label="Down payment (BHD)")
    installment_count = forms.IntegerField(min_value=1, max_value=120, label="Number of installments")
    installment_amount = forms.DecimalField(label="Installment (BHD)", **MONEY)
    final_installment_amount = forms.DecimalField(
        required=False, label="Last installment (BHD)", help_text="Only if the last one differs from the others.",
        **MONEY)
    first_due_on = forms.DateField(widget=date_input(), label="First installment due on",
                                   help_text="Later ones follow on the same day of each month.")
    already_paid = forms.IntegerField(
        required=False, min_value=0, initial=0, label="Installments already paid",
        help_text="For a loan that started before this system was used: the first ones are marked as paid.")
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))

    def clean_down_payment(self):
        return self.cleaned_data.get("down_payment") or Decimal("0")

    def clean_already_paid(self):
        return self.cleaned_data.get("already_paid") or 0


class PayInstallmentForm(forms.Form):
    paid_on = forms.DateField(widget=date_input(max="today"), label="Paid on")
    amount = forms.DecimalField(label="Amount paid (BHD)", **MONEY)
    reference = forms.CharField(required=False, max_length=60, label="Payment reference")

    def __init__(self, *args, installment, **kwargs):
        super().__init__(*args, **kwargs)
        self.initial.setdefault("paid_on", timezone.localdate())
        self.initial.setdefault("amount", installment.amount)


class SettleForm(forms.Form):
    settled_on = forms.DateField(widget=date_input(max="today"), label="Settled on")
    amount = forms.DecimalField(label="Settlement amount paid (BHD)", **MONEY)
    reference = forms.CharField(required=False, max_length=60, label="Settlement reference")

    def __init__(self, *args, outstanding, **kwargs):
        super().__init__(*args, **kwargs)
        self.initial.setdefault("settled_on", timezone.localdate())
        self.fields["amount"].help_text = (
            f"The figure the bank quoted. The installments still due add up to {outstanding:.3f} BHD.")
