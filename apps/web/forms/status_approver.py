from django import forms

from apps.organization.services import companies_for

from apps.web.forms.compliance import responsible_users


class StatusApproverForm(forms.Form):
    company = forms.ModelChoiceField(queryset=None, label="Company")
    approver = forms.ModelChoiceField(
        queryset=None, required=False, label="Who approves family status",
        empty_label="The employee's reporting manager (the default)",
        help_text="One person can approve every family status request of the company. Leave it on the default to "
                  "let each employee's reporting manager decide.")

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        companies = companies_for(user)
        self.fields["company"].queryset = companies
        self.fields["approver"].queryset = responsible_users(user)
        self.fields["approver"].label_from_instance = lambda u: u.get_full_name() or u.get_username()
        ids = list(companies.values_list("pk", flat=True)[:2])
        if len(ids) == 1:
            self.initial.setdefault("company", ids[0])
            self.fields["company"].widget = forms.HiddenInput()
