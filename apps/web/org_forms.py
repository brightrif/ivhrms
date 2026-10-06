from django import forms
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator

from apps.organization import services
from apps.organization.models import Company, Department, Designation


class DepartmentForm(forms.ModelForm):
    class Meta:
        model = Department
        fields = ["company", "name", "code", "parent"]
        labels = {"parent": "Sub-department of"}

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        company_field, code, parent = self.fields["company"], self.fields["code"], self.fields["parent"]
        code.required = False
        parent.required = False
        parent.empty_label = "None (top level)"

        if self.instance.pk:                                   # editing: company and code are fixed
            company = self.instance.company
            company_field.queryset = Company.objects.filter(pk=company.pk)
            company_field.disabled = True
            code.disabled = True
            code.help_text = "The code cannot be changed after the department is created."
            blocked = {self.instance.pk} | services.descendant_ids(self.instance)
            parent.queryset = Department.objects.filter(company=company).exclude(pk__in=blocked)
            return

        companies = services.companies_for(user)
        company_field.queryset = companies
        ids = list(companies.values_list("pk", flat=True)[:2])
        if len(ids) == 1:                                      # one company: nothing to ask
            company_field.widget = forms.HiddenInput()
            self.initial.setdefault("company", ids[0])
        else:
            parent.label_from_instance = lambda d: f"{d.name} ({d.company.code})"
        parent.queryset = Department.objects.filter(company__in=companies).select_related("company")
        code.help_text = "Optional. Leave blank to generate one from the name. Cannot be changed later."
        code.validators.append(RegexValidator(r"^[A-Za-z0-9_-]+$",
                                              "Use letters, numbers, hyphen or underscore only."))

    def clean_code(self):
        if self.instance.pk:
            return self.instance.code
        return (self.cleaned_data.get("code") or "").strip().upper()

    def clean(self):
        cd = super().clean()
        company = cd.get("company") or (self.instance.company if self.instance.pk else None)
        name = " ".join((cd.get("name") or "").split())
        if name:
            cd["name"] = name
        if company and name and (Department.objects.filter(company=company, name__iexact=name)
                                 .exclude(pk=self.instance.pk).exists()):
            self.add_error("name", "This company already has a department with that name.")
        parent = cd.get("parent")
        if company and parent and parent.company_id != company.pk:
            self.add_error("parent", "Must belong to the same company.")
        if company and name and not self.instance.pk and not cd.get("code"):
            cd["code"] = services.suggest_department_code(company, name)
        return cd


class DesignationForm(forms.ModelForm):
    class Meta:
        model = Designation
        fields = ["name"]

    def clean_name(self):
        name = " ".join(self.cleaned_data["name"].split())
        if Designation.objects.filter(name__iexact=name).exclude(pk=self.instance.pk).exists():
            raise ValidationError("That designation already exists.")
        return name