import re

from django import forms
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.urls import reverse

from apps.organization import services
from apps.organization.models import Company


class CompanyForm(forms.ModelForm):
    class Meta:
        model = Company
        fields = ["name", "name_ar", "code", "currency", "cr_number", "sio_number", "address"]
        widgets = {"address": forms.Textarea(attrs={"rows": 3})}
        labels = {"cr_number": "Commercial registration (CR) no.", "sio_number": "SIO employer no."}
        help_texts = {"code": "Short identifier used in usernames and reports, e.g. IV. Cannot be changed later.",
                      "currency": "3-letter code, e.g. BHD."}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields["code"].disabled = True
            self.fields["code"].help_text = "The code cannot be changed after the company is created."
        else:
            code = self.fields["code"]
            code.required = False
            code.validators.append(RegexValidator(
                r"^[A-Za-z0-9_-]+$", "Use letters, numbers, hyphen or underscore only (no spaces)."))
            code.help_text = ("Filled in from the name as you type. You can change it before saving. "
                              "It cannot be changed after the company is created.")
            code.widget.attrs.update({
                "data-autofill-from": "id_name",
                "data-autofill-url": reverse("web:company_code_preview"),
                "autocomplete": "off",
                "style": "text-transform:uppercase",
            })

    def clean_code(self):
        if self.instance.pk:
            return self.instance.code
        return self.cleaned_data["code"].strip().upper()

    def clean_name(self):
        name = " ".join(self.cleaned_data["name"].split())
        if Company.objects.filter(name__iexact=name).exclude(pk=self.instance.pk).exists():
            raise ValidationError("A company with this name already exists.")
        return name

    def clean_currency(self):
        value = self.cleaned_data["currency"].strip().upper()
        if not re.fullmatch(r"[A-Z]{3}", value):
            raise ValidationError("Use a 3-letter currency code such as BHD.")
        return value
    
    def clean(self):
        cd = super().clean()
        if not self.instance.pk and cd.get("name") and not cd.get("code"):
            cd["code"] = services.suggest_company_code(cd["name"])
        return cd