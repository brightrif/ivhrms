from django import forms


class StatusRequestForm(forms.Form):
    reason = forms.CharField(
        required=False, label="Why is family status needed?", widget=forms.Textarea(attrs={"rows": 3}),
        help_text="For example: the wife and children will live in Bahrain. Optional, but it helps the approvers.")


class DeclineForm(forms.Form):
    note = forms.CharField(
        label="Reason", widget=forms.Textarea(attrs={"rows": 3}),
        help_text="The employee will see this.")
