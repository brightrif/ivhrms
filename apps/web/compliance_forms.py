from datetime import timedelta
from decimal import Decimal

from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.utils import timezone
from django.utils.text import slugify

from apps.compliance import schedule, services
from apps.compliance.models import Document, DocumentType, RenewalPayment, RenewalTask
from apps.compliance.validators import validate_extension, validate_file_size
from apps.organization.models import Company
from apps.organization.services import companies_for

from .forms import date_input

# A plain FileInput on purpose: the "Currently: <link>" widget would ask for a public URL, and these files have none.
SCAN_ATTRS = {"accept": ".pdf,.jpg,.jpeg,.png"}
SCAN_HELP = "PDF, JPG or PNG, up to 10 MB."


def responsible_users(user, include_pk=None):
    User = get_user_model()
    condition = Q(is_superuser=True) | Q(company_access__company__in=companies_for(user))
    if include_pk:
        condition |= Q(pk=include_pk)
    return (User.objects.filter(is_active=True).filter(condition).distinct()
            .order_by("first_name", "username"))


def _person(u):
    return u.get_full_name() or u.username


def _people_field(field, user, include_pk=None, empty="Not assigned"):
    field.queryset = responsible_users(user, include_pk)
    field.label_from_instance = _person
    field.required = False
    field.empty_label = empty


class DocumentForm(forms.ModelForm):
    class Meta:
        model = Document
        fields = ["company", "document_type", "reference_name", "number", "issue_date", "expiry_date",
                  "responsible", "agent_name", "file", "notes"]
        widgets = {
            "issue_date": date_input(max="today"), "expiry_date": date_input(min_from="id_issue_date"),
            "file": forms.FileInput(attrs=SCAN_ATTRS), "notes": forms.Textarea(attrs={"rows": 2}),
        }
        labels = {"document_type": "Document type", "agent_name": "Handled by (agent / PRO)",
                  "file": "Scanned copy", "number": "Certificate / reference number"}
        help_texts = {"reference_name": "Only if the company holds several of this type, e.g. a vehicle plate.",
                      "file": SCAN_HELP}

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        company, dtype = self.fields["company"], self.fields["document_type"]
        if self.instance.pk:                         # the company and type of an existing document are fixed
            company.queryset = Company.objects.filter(pk=self.instance.company_id)
            dtype.queryset = DocumentType.objects.filter(pk=self.instance.document_type_id)
            company.disabled = dtype.disabled = True
        else:
            companies = companies_for(user)
            company.queryset = companies
            ids = list(companies.values_list("pk", flat=True)[:2])
            if len(ids) == 1:
                self.initial.setdefault("company", ids[0])
                company.widget = forms.HiddenInput()
            dtype.queryset = DocumentType.objects.filter(is_active=True,
                                                         applies_to=DocumentType.AppliesTo.COMPANY)
            self.initial.setdefault("responsible", user.pk)
        _people_field(self.fields["responsible"], user, self.instance.responsible_id)

    def clean_reference_name(self):
        return " ".join(self.cleaned_data.get("reference_name", "").split())

    def clean(self):
        cd = super().clean()
        issue, expiry = cd.get("issue_date"), cd.get("expiry_date")
        if issue and expiry and expiry < issue:
            self.add_error("expiry_date", "The expiry date cannot be before the issue date.")
        company, dtype = cd.get("company"), cd.get("document_type")
        if company and dtype:
            # Checked here because Django skips a conditional unique rule whose condition field is not on the form
            clash = Document.objects.filter(company=company, document_type=dtype, is_current=True,
                                            reference_name=cd.get("reference_name", ""))
            if self.instance.pk:
                clash = clash.exclude(pk=self.instance.pk)
            if clash.exists():
                ref = cd.get("reference_name")
                raise ValidationError(
                    f"{company.name} already has a current document of this type"
                    + (f" named '{ref}'" if ref else "")
                    + ". Renew it instead, or give this one a different reference name.")
        return cd


class RenewForm(forms.Form):
    number = forms.CharField(required=False, max_length=60, label="Certificate / reference number")
    issue_date = forms.DateField(required=False, widget=date_input(max="today"))
    expiry_date = forms.DateField(widget=date_input(min_from="id_issue_date"), label="New expiry date")
    file = forms.FileField(required=False, label="Scanned copy", widget=forms.FileInput(attrs=SCAN_ATTRS),
                           validators=[validate_extension, validate_file_size], help_text=SCAN_HELP)
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, document, **kwargs):
        super().__init__(*args, **kwargs)
        self.document = document
        # a renewal has to extend the validity, so earlier days cannot be picked
        self.fields["expiry_date"].widget.attrs["data-min"] = (document.expiry_date + timedelta(days=1)).isoformat()
        months = document.document_type.default_validity_months
        self.initial.update(number=document.number, issue_date=timezone.localdate(),
                            expiry_date=services.add_months(document.expiry_date, months) if months else None)

    def clean(self):
        cd = super().clean()
        issue, expiry = cd.get("issue_date"), cd.get("expiry_date")
        if expiry and expiry <= self.document.expiry_date:
            self.add_error("expiry_date",
                           f"Must be later than the current expiry date ({self.document.expiry_date:%d %b %Y}).")
        elif issue and expiry and expiry < issue:
            self.add_error("expiry_date", "The expiry date cannot be before the issue date.")
        return cd


class RenewalTaskForm(forms.ModelForm):
    class Meta:
        model = RenewalTask
        fields = ["assignee", "due_date", "estimated_cost", "notes"]
        widgets = {"due_date": date_input(), "notes": forms.Textarea(attrs={"rows": 2})}
        labels = {"estimated_cost": "Estimated cost (BHD)"}

    def __init__(self, *args, user, document, **kwargs):
        super().__init__(*args, **kwargs)
        _people_field(self.fields["assignee"], user, document.responsible_id, empty="Not assigned")
        self.initial.setdefault("assignee", document.responsible_id)
        self.initial.setdefault("due_date", document.expiry_date)


class PaymentForm(forms.ModelForm):
    class Meta:
        model = RenewalPayment
        fields = ["description", "paid_on", "government_fee", "service_fee", "fine", "method",
                  "reference", "receipt", "paid_by", "remarks"]
        widgets = {"paid_on": date_input(max="today"), "receipt": forms.FileInput(attrs=SCAN_ATTRS),
                   "remarks": forms.Textarea(attrs={"rows": 2})}
        labels = {"government_fee": "Government fee (BHD)", "service_fee": "Service / agent fee (BHD)",
                  "fine": "Late fine (BHD)", "reference": "Payment reference", "paid_by": "Paid by"}
        help_texts = {"receipt": SCAN_HELP}

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        # (setdefault would not work here: the form is built around a blank payment, whose fields are already None)
        if not self.initial.get("paid_on"):
            self.initial["paid_on"] = timezone.localdate()
        if not self.initial.get("paid_by"):
            self.initial["paid_by"] = user.pk
        _people_field(self.fields["paid_by"], user, empty="Not recorded")
        for name in ("government_fee", "service_fee", "fine"):
            self.fields[name].required = False          # a cleared box means zero

    def clean_government_fee(self):
        return self.cleaned_data.get("government_fee") or Decimal("0")

    def clean_service_fee(self):
        return self.cleaned_data.get("service_fee") or Decimal("0")

    def clean_fine(self):
        return self.cleaned_data.get("fine") or Decimal("0")

    def clean(self):
        cd = super().clean()
        amounts = [cd.get(f) or Decimal("0") for f in ("government_fee", "service_fee", "fine")]
        if sum(amounts, Decimal("0")) <= 0:
            raise ValidationError("Enter at least one amount greater than zero.")
        if cd.get("paid_on") and cd["paid_on"] > timezone.localdate():
            self.add_error("paid_on", "The payment date cannot be in the future.")
        return cd


class DocumentTypeForm(forms.ModelForm):
    alert_days = forms.CharField(
        required=False, label="Alert days before expiry",
        help_text="Comma-separated, e.g. 30, 14, 7, 1, 0. Use 0 for the expiry day. Leave blank to switch alerts off.")

    class Meta:
        model = DocumentType
        fields = ["code", "name", "name_ar", "authority", "default_validity_months",
                  "alert_days", "overdue_repeat_days", "is_mandatory"]
        labels = {"code": "Short code", "default_validity_months": "Usual validity (months)",
                  "overdue_repeat_days": "Repeat overdue (days)"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields["code"].disabled = True
            self.initial["alert_days"] = ", ".join(str(n) for n in self.instance.alert_days)
        else:
            self.fields["code"].required = False
            self.fields["code"].help_text = "Optional. Generated from the name if left blank. Cannot be changed later."
            self.initial.setdefault("alert_days", ", ".join(str(n) for n in schedule.DEFAULT_ALERT_DAYS))

    def clean_name(self):
        name = " ".join(self.cleaned_data["name"].split())
        if DocumentType.objects.filter(name__iexact=name).exclude(pk=self.instance.pk).exists():
            raise ValidationError("A document type with this name already exists.")
        return name

    def clean_alert_days(self):
        try:
            return schedule.normalise_alert_days(self.cleaned_data.get("alert_days"))
        except ValueError as exc:
            raise ValidationError(str(exc))

    def clean_code(self):
        if self.instance.pk:
            return self.instance.code
        typed = slugify(self.cleaned_data.get("code") or "")[:40]
        if typed:
            if DocumentType.objects.filter(code=typed).exists():
                raise ValidationError("That code is already used by another document type.")
            return typed
        code = slugify(self.data.get("name", ""))[:40]
        if not code:
            raise ValidationError("Enter a name first.")
        base, n = code, 1
        while DocumentType.objects.filter(code=code).exists():
            n += 1
            code = f"{base[:36]}-{n}"
        return code
