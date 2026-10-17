import json
from decimal import Decimal

from django import forms
from django.db.models import Q
from django.utils import timezone

from apps.configuration import services as settings_service
from apps.labor.models import Contractor, LaborProfile, LaborRate, Trade
from apps.organization.models import Company
from apps.organization.services import companies_for, default_company_id
from apps.web.forms.nationality import NationalityInput, NationalityMixin

from apps.web.forms.common import date_input
from apps.web.forms.nationality import NationalityField
from apps.web.forms.phone import phone_field
from apps.employees import numbering

DAILY = LaborRate.WageBasis.DAILY


def _worker_defaults():
    """What the add-worker forms start with, from Settings."""
    return {"standard_hours": settings_service.get("labor.default_standard_hours")}


def _contractor_label(c):
    return f"{c.name} ({c.company.code})"


def _yes_no(label, help_text=""):
    # Radio buttons with nothing pre-selected, so HR has to choose and nobody gets the wrong terms by accident.
    return forms.TypedChoiceField(choices=[("1", "Yes"), ("0", "No")], coerce=lambda v: v == "1",
                                  widget=forms.RadioSelect, label=label, help_text=help_text,
                                  error_messages={"required": "Choose Yes or No."})


def _yn(value):
    return "1" if value else "0"


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
        qs = contractors.filter(is_active=True).select_related("company")
        self.fields["contractor"].queryset = qs
        # read by the script in form_layout.html to pre-fill the switches when a contractor is chosen
        self.fields["contractor"].widget.attrs["data-defaults"] = json.dumps({
            str(c.pk): {"overtime": c.default_overtime, "premium": c.default_weekend_premium} for c in qs})
        trades = Trade.objects.filter(Q(is_active=True) | Q(pk=current_trade.pk if current_trade else None))
        self.fields["trade"].queryset = trades.order_by("name")

    def clean(self):
        cd = super().clean()
        engagement, contractor = cd.get("engagement"), cd.get("contractor")
        if engagement == "direct" and contractor:
            self.add_error("contractor", "A direct employee has no contractor. Choose 'Contracted' or clear this.")
        if engagement == "contracted" and cd.get("wage_basis") == "monthly":
            self.add_error("wage_basis", "Contracted workers are paid hourly or by the day.")
        return cd


class _TermsForm(forms.Form):
    """What the worker is paid."""
    wage_basis = forms.ChoiceField(choices=LaborRate.WageBasis.choices, initial=DAILY, label="Pay basis")
    rate = forms.DecimalField(min_value=Decimal("0.001"), max_digits=10, decimal_places=3, label="Rate (BHD)",
                              help_text="Per hour, per day or per month, depending on the pay basis.")
    standard_hours = forms.DecimalField(min_value=Decimal("1"), max_value=Decimal("24"), max_digits=4,
                                        decimal_places=2, initial=Decimal("8"), label="Standard hours per day",
                                        help_text="Hours beyond this count as overtime.")
    overtime_eligible = _yes_no("Overtime", "Yes: hours beyond the standard day are paid at the overtime multiplier. "
                                             "No: every hour is paid at the normal hourly rate.")
    weekend_premium = _yes_no("Friday and holiday premium",
                              "Yes: Fridays and public holidays are paid at the company premium multiplier.")
    paid_holidays = _yes_no("Paid for holidays and paid leave",
                            "Yes: public holidays and paid leave are paid at the standard hours.")


class LaborWorkerForm(NationalityMixin, _EngagementForm, _TermsForm):
    """A brand-new worker: the person and the labor profile in one go."""
    company = forms.ModelChoiceField(queryset=Company.objects.none())
    first_name = forms.CharField(max_length=80)
    last_name = forms.CharField(max_length=80, required=False)
    nationality = NationalityField()
    phone = phone_field()
    joining_date = forms.DateField(widget=date_input(max="today"))

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        companies = companies_for(user)
        self.fields["company"].queryset = companies
        ids = list(companies.values_list("pk", flat=True)[:2])
        if len(ids) == 1:
            self.initial.setdefault("company", ids[0])
            self.fields["company"].widget = forms.HiddenInput()
        self.initial.setdefault("company", default_company_id(user, "labor"))
        self.initial.setdefault("joining_date", timezone.localdate())
        for name, value in _worker_defaults().items():
            self.initial.setdefault(name, value)
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
        for name, value in _worker_defaults().items():
            self.initial.setdefault(name, value)
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
                                standard_hours=current.standard_hours, overtime_eligible=_yn(current.overtime_eligible),
                                weekend_premium=_yn(current.weekend_premium),
                                paid_holidays=_yn(current.paid_holidays))
            self.fields["effective_from"].widget.attrs["data-min"] = (current.effective_from).isoformat()
        self.initial.setdefault("effective_from", timezone.localdate())
        if profile.engagement == "contracted":                 # contracted workers are always paid by the day
            self.fields["wage_basis"].choices = [c for c in LaborRate.WageBasis.choices
                                                 if c[0] != LaborRate.WageBasis.MONTHLY]
            if self.initial.get("wage_basis") not in (DAILY, LaborRate.WageBasis.HOURLY):
                self.initial["wage_basis"] = DAILY


_KEEP_YES_NO = [("", "Leave as it is"), ("1", "Yes"), ("0", "No")]


def _change_choice(label):
    return forms.TypedChoiceField(choices=_KEEP_YES_NO, required=False, coerce=lambda v: v == "1",
                                  empty_value=None, label=label)


class BulkSwitchesForm(forms.Form):
    """The pay switches of several ticked workers, from one date. A switch left as it is stays as it is."""
    profiles = forms.ModelMultipleChoiceField(queryset=LaborProfile.objects.none(), widget=forms.MultipleHiddenInput)
    effective_from = forms.DateField(widget=date_input(), label="Starts on")
    overtime_eligible = _change_choice("Overtime")
    weekend_premium = _change_choice("Friday and holiday premium")
    paid_holidays = _change_choice("Paid for holidays and paid leave")

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["profiles"].queryset = LaborProfile.objects.for_user(user).select_related("employee")
        self.initial.setdefault("effective_from", timezone.localdate())

    def clean(self):
        cd = super().clean()
        if not self.errors and all(cd.get(n) is None for n in ("overtime_eligible", "weekend_premium", "paid_holidays")):
            raise forms.ValidationError("Choose Yes or No for at least one switch.")
        return cd


_DEFAULT_CHOICES = [("", "No default (HR chooses each time)"), ("True", "Yes"), ("False", "No")]


class ContractorForm(forms.ModelForm):
    phone = phone_field()
    class Meta:
        model = Contractor
        fields = ["company", "name", "name_ar", "cr_number", "contact_person", "phone", "email", "address", "notes",
                  "default_overtime", "default_weekend_premium"]
        widgets = {"address": forms.Textarea(attrs={"rows": 2}), "notes": forms.Textarea(attrs={"rows": 2}),
                   "default_overtime": forms.Select(choices=_DEFAULT_CHOICES),
                   "default_weekend_premium": forms.Select(choices=_DEFAULT_CHOICES)}

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

TERM_FIELDS = list(_TermsForm.base_fields)         # the pay fields, whatever this form has grown to include


class ToLaborForm(_EngagementForm, _TermsForm):
    """A staff member moves to site work. Someone who was labor before comes back on the pay they had then (HR changes
    it afterwards under Change pay), so the pay boxes are shown only for someone who was never labor."""
    effective_from = forms.DateField(widget=date_input(max="today"), label="Labor from")
    number = forms.ChoiceField(label="Employee number")
    remarks = forms.CharField(required=False, max_length=150, label="Note")

    def __init__(self, *args, employee, user, previous=None, earlier=None, **kwargs):
        super().__init__(*args, **kwargs)
        choices = [("new", f"New labor number ({numbering.peek(employee.company, 'labor')})"),
                   ("keep", f"Keep {employee.employee_no}")]
        if previous:
            choices.insert(0, ("previous", f"Their earlier labor number ({previous})"))
        self.fields["number"].choices = choices
        self.initial.setdefault("effective_from", timezone.localdate())
        self.initial.setdefault("number", "previous" if previous else "new")
        for name, value in _worker_defaults().items():
            self.initial.setdefault(name, value)
        old = getattr(employee, "labor_profile", None)                 # a former labor record pre-fills the form
        if old:
            self.initial.update(engagement=old.engagement, contractor=old.contractor_id,
                                trade=old.trade_id, notes=old.notes)
        if earlier:
            for name in TERM_FIELDS:
                self.fields.pop(name, None)
        self._set_choices(Contractor.objects.for_user(user).filter(company_id=employee.company_id))

class WorkerDetailsForm(forms.Form):
    """The Worker card: name, nationality, phone and joining date."""
    first_name = forms.CharField(max_length=80)
    last_name = forms.CharField(max_length=80, required=False)
    nationality = NationalityField()
    phone = phone_field()
    joining_date = forms.DateField(
        widget=date_input(max="today"),
        help_text="An earlier date also moves the first assignment, shift and pay line. A later date is refused "
                  "if something is already recorded before it.")

    def __init__(self, *args, employee, **kwargs):
        super().__init__(*args, **kwargs)
        self.initial.update(first_name=employee.first_name, last_name=employee.last_name,
                            nationality=employee.nationality, phone=employee.phone,
                            joining_date=employee.joining_date)
        self.fields["nationality"].keep = employee.nationality          # an older saved value stays valid


class CorrectPayForm(_TermsForm):
    """Fix a mistake in the current pay line, in place. New pay from a later date is Change pay."""

    def __init__(self, *args, profile, **kwargs):
        super().__init__(*args, **kwargs)
        current = profile.current_rate
        if current:
            self.initial.update({n: getattr(current, n) for n in TERM_FIELDS if hasattr(current, n)})
        if profile.engagement == "contracted":                          # contracted workers are always paid by the day
            self.fields["wage_basis"].choices = [(DAILY, "Daily wage")]


class LaborFullEditForm(WorkerDetailsForm, _EngagementForm, _TermsForm):
    """Everything on the labor page at once: worker, engagement and the current pay line."""

    def __init__(self, *args, employee, profile, user, **kwargs):
        super().__init__(*args, employee=employee, **kwargs)
        self.initial.update(engagement=profile.engagement, contractor=profile.contractor_id,
                            trade=profile.trade_id, notes=profile.notes)
        current = profile.current_rate
        if current:
            self.initial.update({n: getattr(current, n) for n in TERM_FIELDS if hasattr(current, n)})
        else:
            for name in TERM_FIELDS:
                self.fields.pop(name, None)
        self._set_choices(Contractor.objects.for_user(user).filter(company_id=profile.company_id),
                          current_trade=profile.trade)