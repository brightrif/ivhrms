import re

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.validators import UnicodeUsernameValidator
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

from apps.employees.models import Employee, EmploymentRecord
from apps.organization.models import Department, Designation, Grade, Location
from apps.organization.services import companies_for

from .forms import date_input

ASSIGNMENT_FIELDS = ("employment_type", "department", "designation", "grade", "location", "reporting_manager")


def normalize_whatsapp(raw):
    """Digits only, with country code: '+973 3336 6235' -> '97333366235'."""
    if not raw:
        return None
    digits = re.sub(r"\D", "", raw)
    if not 8 <= len(digits) <= 15:
        raise ValidationError("Enter the full number with country code, e.g. 97333123456.")
    return digits


class WhatsAppMixin:
    def clean_whatsapp_number(self):
        return normalize_whatsapp(self.cleaned_data.get("whatsapp_number"))


class EmployeeForm(WhatsAppMixin, forms.ModelForm):
    """Quick registration: only what is needed to add the person. The rest is assigned later."""
    create_login = forms.BooleanField(
        required=False, label="Create a web login",
        widget=forms.CheckboxInput(attrs={"role": "switch"}))
    username = forms.CharField(required=False, max_length=150,
                               help_text="Leave blank to use the employee number, e.g. iv-0001.")

    class Meta:
        model = Employee
        fields = ["company", "worker_type", "employment_type", "joining_date", "designation", "department",
                  "first_name", "last_name", "name_ar", "date_of_birth", "nationality",
                  "phone", "whatsapp_number", "email"]
        widgets = {"date_of_birth": date_input(), "joining_date": date_input()}
        help_texts = {"email": "Optional.",
                      "whatsapp_number": "Optional. With country code, e.g. 97333123456."}

    def __init__(self, *args, user, company_id=None, **kwargs):
        super().__init__(*args, **kwargs)
        companies = companies_for(user)
        self.fields["company"].queryset = companies
        self.fields["company"].widget.attrs.update({
            "hx-get": reverse("web:employee_company_fields"),
            "hx-target": "#company-fields", "hx-swap": "innerHTML"})

        raw = company_id or (self.data.get("company") if self.is_bound else None)
        company = companies.filter(pk=raw).first() if str(raw or "").isdigit() else None
        if company is None:
            only = list(companies[:2])
            company = only[0] if len(only) == 1 else None
        if company and not self.is_bound:
            self.initial.setdefault("company", company.pk)

        dept = self.fields["department"]
        dept.queryset = (Department.objects.filter(company=company, is_active=True)
                         if company else Department.objects.none())
        dept.empty_label = "Assign later"
        dept.help_text = "Optional. You can assign or change it later." if company else "Choose a company first."

        desig = self.fields["designation"]
        desig.queryset = Designation.objects.filter(is_active=True)
        desig.required = True
        desig.empty_label = "Select a designation"

    def clean(self):
        cd = super().clean()
        if not cd.get("create_login"):
            cd["username"] = ""                      # a username without a login is ignored
            return cd
        name = (cd.get("username") or "").strip()
        if name:
            try:
                UnicodeUsernameValidator()(name)
            except ValidationError as exc:
                self.add_error("username", exc)
            else:
                if get_user_model().objects.filter(username__iexact=name).exists():
                    self.add_error("username", "That username is already taken.")
                else:
                    cd["username"] = name
        return cd


class EmployeePersonalForm(WhatsAppMixin, forms.ModelForm):
    """Edit form: personal and contact details only. Assignment changes keep their history."""
    class Meta:
        model = Employee
        fields = ["first_name", "last_name", "name_ar", "date_of_birth", "nationality",
                  "email", "phone", "whatsapp_number"]
        widgets = {"date_of_birth": date_input()}


class AssignmentForm(forms.Form):
    effective_from = forms.DateField(widget=date_input())
    reason = forms.ChoiceField(choices=[c for c in EmploymentRecord.Reason.choices
                                        if c[0] != EmploymentRecord.Reason.JOINING])
    employment_type = forms.ChoiceField(choices=Employee.EmploymentType.choices)
    department = forms.ModelChoiceField(queryset=Department.objects.none(), required=False,
                                        empty_label="Not assigned")
    designation = forms.ModelChoiceField(queryset=Designation.objects.filter(is_active=True))
    grade = forms.ModelChoiceField(queryset=Grade.objects.all(), required=False)
    location = forms.ModelChoiceField(queryset=Location.objects.filter(is_active=True), required=False)
    reporting_manager = forms.ModelChoiceField(queryset=Employee.objects.none(), required=False)
    remarks = forms.CharField(required=False, max_length=255)

    def __init__(self, *args, employee, **kwargs):
        super().__init__(*args, **kwargs)
        self.employee = employee
        self.fields["department"].queryset = Department.objects.filter(company=employee.company, is_active=True)
        self.fields["reporting_manager"].queryset = (
            Employee.objects.filter(company=employee.company)
            .exclude(pk=employee.pk).exclude(status=Employee.Status.SEPARATED))
        self.initial.update(
            effective_from=timezone.localdate(), employment_type=employee.employment_type,
            department=employee.department_id, designation=employee.designation_id,
            grade=employee.grade_id, location=employee.location_id,
            reporting_manager=employee.reporting_manager_id)

    def clean(self):
        cd = super().clean()
        emp, eff = self.employee, cd.get("effective_from")
        if eff and eff > timezone.localdate():
            self.add_error("effective_from",
                           "Future-dated changes are not supported yet. Use today's date or earlier.")
        current = emp.history.filter(effective_to__isnull=True).first()
        if eff and current and eff <= current.effective_from:
            self.add_error("effective_from",
                           f"Must be after the current record's start date ({current.effective_from:%d %b %Y}).")
        if all(f in cd for f in ASSIGNMENT_FIELDS):
            if all(cd[f] == getattr(emp, f) for f in ASSIGNMENT_FIELDS):
                raise ValidationError("Nothing has changed.")
            mgr, seen = cd["reporting_manager"], set()
            while mgr is not None and mgr.pk not in seen:      # walk up the reporting line
                if mgr.pk == emp.pk:
                    self.add_error("reporting_manager", "That would make the reporting line circular.")
                    break
                seen.add(mgr.pk)
                mgr = mgr.reporting_manager
        return cd