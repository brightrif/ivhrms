from decimal import Decimal

from django import forms
from django.db.models import Q
from django.utils import timezone

from apps.labor.models import Contractor, LaborProfile, LaborRate, Trade
from apps.organization.models import Company
from apps.organization.services import companies_for

from apps.web.forms.common import date_input

DAILY = LaborRate.WageBasis.DAILY


def _contractor_label(c):
    return f"{c.name} ({c.company.code})"


class _EngagementForm(forms.Form):
    """How the worker is engaged, who supplies them and their trade."""
    engagement = forms.ChoiceField(choices=[], label="Engagement")
    contractor = forms.ModelChoiceField(queryset=Contractor.objects.none(), required=False,
                                        empty_label="No contractor (engaged directly)",
                                        help_text="Only if a contractor company supplies this worker.")
    trade = forms.ModelChoiceField(queryset=Trade.objects.none(), empty_label="Choose a trade")
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["engagement"].choices = LaborProfile.Engagement.choices
        self.fields["contractor"].label_from_instance = _contractor_label

    def _set_choices(self, contractors, current_trade=None):
        self.fields["contractor"].queryset = contractors.filter(is_active=True).select_related("company")
        trades = Trade.objects.filter(Q(is_active=True) | Q(pk=current_trade.pk if current_trade else None))
        self.fields["trade"].queryset = trades.order_by("name")

    def clean(self):
        cd = super().clean()
        engagement, contractor = cd.get("engagement"), cd.get("contractor")
        if engagement == "direct" and contractor:
            self.add_error("contractor", "A direct employee has no contractor. Choose 'Contracted' or clear this.")
        if engagement == "contracted" and cd.get("wage_basis") not in (None, DAILY):
            self.add_error("wage_basis", "Contracted workers are paid a daily rate.")
        return cd


class _TermsForm(forms.Form):
    """What the worker is paid."""
    wage_basis = forms.ChoiceField(choices=LaborRate.WageBasis.choices, initial=DAILY, label="Pay basis")
    rate = forms.DecimalField(min_value=Decimal("0.001"), max_digits=10, decimal_places=3, label="Rate (BHD)",
                              help_text="Per day for a daily wage, per month for a monthly salary.")
    standard_hours = forms.DecimalField(min_value=Decimal("1"), max_value=Decimal("24"), max_digits=4,
                                        decimal_places=2, initial=Decimal("8"), label="Standard hours per day",
                                        help_text="Hours beyond this count as overtime.")
    overtime_eligible = forms.BooleanField(required=False, initial=True, label="Eligible for overtime")


class LaborWorkerForm(_EngagementForm, _TermsForm):
    """A brand-new worker: the person and the labor profile in one go."""
    company = forms.ModelChoiceField(queryset=Company.objects.none())
    first_name = forms.CharField(max_length=80)
    last_name = forms.CharField(max_length=80, required=False)
    nationality = forms.CharField(max_length=60, required=False)
    phone = forms.CharField(max_length=30, required=False)
    joining_date = forms.DateField(widget=date_input(max="today"))

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        companies = companies_for(user)
        self.fields["company"].queryset = companies
        ids = list(companies.values_list("pk", flat=True)[:2])
        if len(ids) == 1:
            self.initial.setdefault("company", ids[0])
            self.fields["company"].widget = forms.HiddenInput()
        self.initial.setdefault("joining_date", timezone.localdate())
        self._set_choices(Contractor.objects.for_user(user))

    def clean(self):
        cd = super().clean()
        company, contractor = cd.get("company"), cd.get("contractor")
        if company and contractor and contractor.company_id != company.pk:
            self.add_error("contractor", "This contractor belongs to another company.")
        return cd


class ProfileSetupForm(_EngagementForm, _TermsForm):
    """A labor employee that has no profile yet."""
    effective_from = forms.DateField(widget=date_input(), label="Pay starts on")

    def __init__(self, *args, employee, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.initial.setdefault("effective_from", employee.joining_date)
        self._set_choices(Contractor.objects.for_user(user).filter(company_id=employee.company_id))


class ProfileEditForm(_EngagementForm):
    def __init__(self, *args, profile, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.initial.update(engagement=profile.engagement, contractor=profile.contractor_id,
                            trade=profile.trade_id, notes=profile.notes)
        self._set_choices(Contractor.objects.for_user(user).filter(company_id=profile.company_id),
                          current_trade=profile.trade)


class RateForm(_TermsForm):
    effective_from = forms.DateField(widget=date_input(), label="Starts on")

    def __init__(self, *args, profile, **kwargs):
        super().__init__(*args, **kwargs)
        current = profile.current_rate
        if current:
            self.initial.update(wage_basis=current.wage_basis, rate=current.rate,
                                standard_hours=current.standard_hours, overtime_eligible=current.overtime_eligible)
            self.fields["effective_from"].widget.attrs["data-min"] = (current.effective_from).isoformat()
        self.initial.setdefault("effective_from", timezone.localdate())
        if profile.engagement == "contracted":                 # contracted workers are always paid by the day
            self.fields["wage_basis"].choices = [(DAILY, "Daily wage")]
            self.initial["wage_basis"] = DAILY


class ContractorForm(forms.ModelForm):
    class Meta:
        model = Contractor
        fields = ["company", "name", "name_ar", "cr_number", "contact_person", "phone", "email", "address", "notes"]
        widgets = {"address": forms.Textarea(attrs={"rows": 2}), "notes": forms.Textarea(attrs={"rows": 2})}

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        company = self.fields["company"]
        if self.instance.pk:                      # a contractor stays with the company it was created for
            company.queryset = Company.objects.filter(pk=self.instance.company_id)
            company.disabled = True
        else:
            companies = companies_for(user)
            company.queryset = companies
            ids = list(companies.values_list("pk", flat=True)[:2])
            if len(ids) == 1:
                self.initial.setdefault("company", ids[0])
                company.widget = forms.HiddenInput()

    def clean_name(self):
        return " ".join(self.cleaned_data["name"].split())


class TradeForm(forms.ModelForm):
    class Meta:
        model = Trade
        fields = ["code", "name", "name_ar"]

    def clean_code(self):
        code = self.cleaned_data["code"].strip().upper()
        if Trade.objects.filter(code=code).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("There is already a trade with this code.")
        return code

    def clean_name(self):
        return " ".join(self.cleaned_data["name"].split())
