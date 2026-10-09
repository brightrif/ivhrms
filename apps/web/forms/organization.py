from django import forms
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator

from apps.organization import services
from apps.organization.models import Company, Department, Designation

from django.db.models import Q
from apps.organization.models import Company, Department, Designation, Grade, Location, Project
from apps.web.forms.common import date_input

from django.urls import reverse
from apps.web.forms.widgets import TypeaheadWidget

CODE_CHARS = RegexValidator(r"^[A-Za-z0-9_-]+$", "Use letters, numbers, hyphen or underscore only.")

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

class ProjectForm(forms.ModelForm):
    class Meta:
        model = Project
        fields = ["company", "name", "location", "code", "start_date", "end_date", "status"]
        widgets = {"start_date": date_input(), "end_date": date_input()}
        labels = {"location": "Site"}

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        company_field, code, loc = self.fields["company"], self.fields["code"], self.fields["location"]
        loc.widget = TypeaheadWidget(reverse("web:site_search"), "Type to find a site")
        if self.is_bound:
            loc.widget.typed = self.data.get("location_text", "")
        loc.queryset = Location.objects.filter(
            Q(is_site=True, is_active=True) | Q(pk=self.instance.location_id)).order_by("name")
        loc.help_text = "Workers allocated to this project go here unless another site is chosen."
        code.required = False
        code.validators.append(CODE_CHARS)
        if self.instance.pk:                                   # editing: company and code are fixed
            company_field.queryset = Company.objects.filter(pk=self.instance.company_id)
            company_field.disabled = True
            code.disabled = True
            code.help_text = "The code cannot be changed after the project is created."
            return
        companies = services.project_companies(user)
        company_field.queryset = companies
        ids = list(companies.values_list("pk", flat=True)[:2])
        if len(ids) == 1:                                      # one company: nothing to ask
            company_field.widget = forms.HiddenInput()
            self.initial.setdefault("company", ids[0])
        code.help_text = "Filled in from the name and site as you type. You can change it before saving."
        code.widget.attrs.update({
            "data-autofill-from": "id_name,id_location_value,id_company",
            "data-autofill-url": reverse("web:project_code_preview"),
            "autocomplete": "off", "style": "text-transform:uppercase"})

    def clean_code(self):
        if self.instance.pk:
            return self.instance.code
        return (self.cleaned_data.get("code") or "").strip().upper()

    def clean(self):
        cd = super().clean()
        name = " ".join((cd.get("name") or "").split())
        if name:
            cd["name"] = name
        start, end = cd.get("start_date"), cd.get("end_date")
        if start and end and end < start:
            self.add_error("end_date", "The end date cannot be before the start date.")
        site = cd.get("location")
        if site is None and self.data.get("location_text", "").strip():
            self.add_error("location", "Choose a site from the list, or clear the box.")
        company = cd.get("company")
        if not self.instance.pk and company and name and not cd.get("code"):
            cd["code"] = services.suggest_project_code(company, name, site)
        return cd


class SiteForm(forms.ModelForm):
    class Meta:
        model = Location
        fields = ["name", "code", "is_site"]
        labels = {"is_site": "Work site or camp (untick for an office)"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        code = self.fields["code"]
        code.required = False
        code.validators.append(CODE_CHARS)
        if self.instance.pk:
            code.disabled = True
            code.help_text = "The code cannot be changed after it is created."
            return
        self.initial.setdefault("is_site", True)
        code.help_text = "Filled in from the name as you type. You can change it before saving."
        code.widget.attrs.update({
            "data-autofill-from": "id_name",
            "data-autofill-url": reverse("web:site_code_preview"),
            "autocomplete": "off",
            "style": "text-transform:uppercase",
        })

    def clean_code(self):
        if self.instance.pk:
            return self.instance.code
        return (self.cleaned_data.get("code") or "").strip().upper()

    def clean(self):
        cd = super().clean()
        name = " ".join((cd.get("name") or "").split())
        if name:
            cd["name"] = name
            if Location.objects.filter(name__iexact=name).exclude(pk=self.instance.pk).exists():
                self.add_error("name", "A site or office with that name already exists.")
            elif not self.instance.pk and not cd.get("code"):
                cd["code"] = services.suggest_site_code(Location.objects.all(), name)
        return cd


class GradeForm(forms.ModelForm):
    class Meta:
        model = Grade
        fields = ["name", "code", "rank"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        code = self.fields["code"]
        code.required = False
        code.validators.append(CODE_CHARS)
        if self.instance.pk:
            code.disabled = True
            code.help_text = "The code cannot be changed after it is created."
        else:
            code.help_text = "Optional. Leave blank to generate one from the name."

    def clean_code(self):
        if self.instance.pk:
            return self.instance.code
        return (self.cleaned_data.get("code") or "").strip().upper()

    def clean(self):
        cd = super().clean()
        name = " ".join((cd.get("name") or "").split())
        if name:
            cd["name"] = name
            if Grade.objects.filter(name__iexact=name).exclude(pk=self.instance.pk).exists():
                self.add_error("name", "That grade already exists.")
            elif not self.instance.pk and not cd.get("code"):
                cd["code"] = services.suggest_code(Grade.objects.all(), name, "GR")
        return cd