from django import forms
from django.core.exceptions import ValidationError

from apps.compliance.models import Document, DocumentType
from apps.organization.models import Company
from apps.organization.services import companies_for
from apps.vehicles import services
from apps.vehicles.models import Vehicle

from apps.web.forms.common import date_input
from apps.web.forms.compliance import SCAN_ATTRS, SCAN_HELP, _people_field


class VehicleForm(forms.ModelForm):
    class Meta:
        model = Vehicle
        fields = ["company", "plate_number", "kind", "make", "model", "year", "colour", "chassis_number",
                  "fuel_type", "ownership", "odometer", "notes"]
        widgets = {"notes": forms.Textarea(attrs={"rows": 2})}
        labels = {"plate_number": "Plate number"}
        help_texts = {"odometer": "In km. After the vehicle is added, fuel, service and assignment entries update it.",
                      "ownership": "Loan details are added in a later step."}

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        company = self.fields["company"]
        if self.instance.pk:                      # company and odometer of an existing vehicle are fixed here
            company.queryset = Company.objects.filter(pk=self.instance.company_id)
            company.disabled = True
            self.fields["odometer"].disabled = True
        else:
            companies = companies_for(user)
            company.queryset = companies
            ids = list(companies.values_list("pk", flat=True)[:2])
            if len(ids) == 1:
                self.initial.setdefault("company", ids[0])
                company.widget = forms.HiddenInput()

    def clean_chassis_number(self):
        return self.cleaned_data.get("chassis_number", "").strip().upper()

    def clean(self):
        cd = super().clean()
        # Checked here because Django skips a conditional unique rule whose condition field is not on the form
        company, plate, chassis = cd.get("company"), cd.get("plate_number"), cd.get("chassis_number")
        if company and plate and services.plate_in_use(company.pk, plate, self.instance.pk):
            self.add_error("plate_number", f"{company.name} already has an unsold vehicle with this plate number.")
        if chassis and services.chassis_in_use(chassis, self.instance.pk):
            self.add_error("chassis_number", "A vehicle with this chassis number is already registered.")
        return cd


class SellForm(forms.Form):
    sold_on = forms.DateField(widget=date_input(max="today"), label="Date sold")


class VehicleDocumentForm(forms.ModelForm):
    """Registration, insurance, inspection... The vehicle (and so the company) is fixed by the page."""

    class Meta:
        model = Document
        fields = ["document_type", "number", "issue_date", "expiry_date", "responsible", "agent_name",
                  "file", "notes"]
        widgets = {
            "issue_date": date_input(max="today"), "expiry_date": date_input(min_from="id_issue_date"),
            "file": forms.FileInput(attrs=SCAN_ATTRS), "notes": forms.Textarea(attrs={"rows": 2}),
        }
        labels = {"document_type": "Document type", "agent_name": "Handled by (agent / PRO)",
                  "file": "Scanned copy", "number": "Certificate / policy number"}
        help_texts = {"file": SCAN_HELP}

    def __init__(self, *args, user, vehicle, **kwargs):
        super().__init__(*args, **kwargs)
        self.vehicle = vehicle
        self.instance.vehicle = vehicle
        self.instance.company_id = vehicle.company_id
        dtype = self.fields["document_type"]
        if self.instance.pk:                      # the type of an existing document is fixed
            dtype.queryset = DocumentType.objects.filter(pk=self.instance.document_type_id)
            dtype.disabled = True
        else:
            dtype.queryset = DocumentType.objects.filter(is_active=True,
                                                         applies_to=DocumentType.AppliesTo.VEHICLE)
            self.initial.setdefault("responsible", user.pk)
        _people_field(self.fields["responsible"], user, self.instance.responsible_id)

    def clean(self):
        cd = super().clean()
        issue, expiry, dtype = cd.get("issue_date"), cd.get("expiry_date"), cd.get("document_type")
        if issue and expiry and expiry < issue:
            self.add_error("expiry_date", "The expiry date cannot be before the issue date.")
        if dtype and not self.instance.pk:
            clash = Document.objects.filter(vehicle=self.vehicle, document_type=dtype, is_current=True,
                                            reference_name="")
            if clash.exists():
                raise ValidationError(f"{self.vehicle.plate_number} already has a current {dtype.name}. "
                                      "Renew it instead.")
        return cd
