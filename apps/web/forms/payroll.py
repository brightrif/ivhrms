from datetime import timedelta
from decimal import Decimal

from django import forms
from django.utils import timezone

from apps.organization.services import companies_for
from apps.payroll import services
from apps.payroll.models import AllowanceType, PaymentDetail, SalaryRecord
from apps.payroll.services import PayrollError

from .common import date_input


def allowance_field(allowance):
    return f"allow_{allowance.pk}"


class AllowanceTypeForm(forms.Form):
    company = forms.ModelChoiceField(queryset=None)
    name = forms.CharField(max_length=80, label="Allowance", help_text="For example Housing or Transport.")
    counts_for_sio = forms.BooleanField(
        required=False, label="Counts towards the SIO wage",
        help_text="Tick if the SIO contribution is worked out on this allowance too. Ask the accountant; "
                  "it only matters for Bahraini staff.")

    counts_for_gratuity = forms.BooleanField(
        required=False, label="Social allowance (counts for gratuity)",
        help_text="Tick for the social allowance, if the company pays one. Gratuity is worked out on basic salary plus "
                  "this allowance only.")

    def __init__(self, *args, user, allowance=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.allowance = allowance
        companies = companies_for(user)
        self.fields["company"].queryset = companies
        if allowance is not None:
            self.fields["company"].queryset = companies.filter(pk=allowance.company_id)
            self.fields["company"].disabled = True
            self.initial.update(company=allowance.company_id, name=allowance.name,
                                counts_for_sio=allowance.counts_for_sio,
                                counts_for_gratuity=allowance.counts_for_gratuity)
        else:
            ids = list(companies.values_list("pk", flat=True)[:2])
            if len(ids) == 1:
                self.initial.setdefault("company", ids[0])
                self.fields["company"].widget = forms.HiddenInput()


class _AllowanceFields(forms.Form):
    """Adds one amount box for each allowance type the company has switched on."""

    def _add_allowances(self, company, current=None):
        have = {a.allowance_type_id: a.amount for a in current.allowances.all()} if current else {}
        self.allowance_types = list(AllowanceType.objects.filter(company=company, is_active=True))
        for allowance in self.allowance_types:
            self.fields[allowance_field(allowance)] = forms.DecimalField(
                required=False, min_value=Decimal("0.001"), max_digits=10, decimal_places=3, label=allowance.name,
                help_text="Leave empty if not paid.")
            if allowance.pk in have:
                self.initial[allowance_field(allowance)] = have[allowance.pk]

    def allowances(self):
        """(allowance type, amount) for every allowance box; an empty box is None."""
        return [(a, self.cleaned_data.get(allowance_field(a))) for a in self.allowance_types]

    @property
    def allowance_names(self):
        return [allowance_field(a) for a in self.allowance_types]


class SalaryChangeForm(_AllowanceFields):
    effective_from = forms.DateField(widget=date_input(), label="Starts on")
    reason = forms.ChoiceField(choices=SalaryRecord.Reason.choices)
    basic = forms.DecimalField(min_value=Decimal("0.001"), max_digits=10, decimal_places=3,
                               label="Basic salary (BHD)")
    remarks = forms.CharField(max_length=255, required=False)

    def __init__(self, *args, employee, today=None, latest=None, **kwargs):
        super().__init__(*args, **kwargs)
        today = today or timezone.localdate()
        self._add_allowances(employee.company, latest)
        if latest is None:
            self.initial.update(effective_from=employee.joining_date, reason=SalaryRecord.Reason.JOINING)
        else:
            start = max(today, latest.effective_from + timedelta(days=1))
            self.initial.update(effective_from=start, reason=SalaryRecord.Reason.INCREASE, basic=latest.basic)


class SalaryCorrectForm(_AllowanceFields):
    basic = forms.DecimalField(min_value=Decimal("0.001"), max_digits=10, decimal_places=3,
                               label="Basic salary (BHD)")
    remarks = forms.CharField(max_length=255, label="What was wrong?",
                              help_text="Kept on the record, so the history makes sense later.")

    def __init__(self, *args, record, **kwargs):
        super().__init__(*args, **kwargs)
        self._add_allowances(record.company, record)
        self.initial["basic"] = record.basic


class PaymentForm(forms.Form):
    method = forms.ChoiceField(choices=PaymentDetail.Method.choices, widget=forms.RadioSelect,
                               label="Paid by")
    bank_name = forms.CharField(max_length=80, required=False)
    iban = forms.CharField(max_length=40, required=False, label="IBAN",
                           help_text="Bahrain IBANs have 22 characters, starting BH.")

    def __init__(self, *args, detail=None, **kwargs):
        super().__init__(*args, **kwargs)
        if detail is not None:
            self.initial.update(method=detail.method, bank_name=detail.bank_name, iban=detail.iban_spaced)

    def clean(self):
        cd = super().clean()
        if cd.get("method") == PaymentDetail.Method.BANK:
            if not cd.get("bank_name"):
                self.add_error("bank_name", "Enter the bank name.")
            try:
                cd["iban"] = services.clean_iban(cd.get("iban"))
            except PayrollError as exc:
                self.add_error("iban", str(exc))
        return cd
