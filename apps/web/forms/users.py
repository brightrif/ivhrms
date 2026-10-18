from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group

from apps.organization.models import Company, CompanyAccess

_CHECK = forms.CheckboxSelectMultiple(attrs={"class": "form-check-input"})


def _groups():
    return forms.ModelMultipleChoiceField(queryset=Group.objects.order_by("name"), required=False, widget=_CHECK,
                                          label="Groups")


def _companies():
    return forms.ModelMultipleChoiceField(queryset=Company.objects.filter(is_active=True).order_by("name"),
                                          required=False, widget=forms.CheckboxSelectMultiple(
                                              attrs={"class": "form-check-input"}), label="Companies")


class UserCreateForm(forms.Form):
    """A login for someone who is not an employee record (for an employee, use 'For an employee')."""
    username = forms.CharField(max_length=150)
    first_name = forms.CharField(max_length=150, required=False)
    last_name = forms.CharField(max_length=150, required=False)
    email = forms.EmailField(required=False)
    groups = _groups()
    companies = _companies()

    def clean_username(self):
        username = self.cleaned_data["username"].strip()
        if get_user_model().objects.filter(username__iexact=username).exists():
            raise forms.ValidationError("That username is already taken.")
        return username


class UserAccessForm(forms.Form):
    """What an existing person may reach: their groups, their companies, and whether the login is on."""
    groups = _groups()
    companies = _companies()
    is_active = forms.BooleanField(required=False, label="Login is switched on",
                                   help_text="Switch it off to stop someone signing in, without deleting anything.")

    def __init__(self, *args, target, **kwargs):
        super().__init__(*args, **kwargs)
        self.initial.setdefault("groups", list(target.groups.values_list("pk", flat=True)))
        self.initial.setdefault("companies", list(CompanyAccess.objects.filter(user=target)
                                                  .values_list("company_id", flat=True)))
        self.initial.setdefault("is_active", target.is_active)
