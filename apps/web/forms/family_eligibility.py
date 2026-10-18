from django import forms

from apps.dependants.sponsor import FamilyVisaException

from apps.web.forms.dependants import DependantForm


class SponsoredDependantForm(DependantForm):
    """Add a family member for an employee who is already known: there is no employee box."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields.pop("employee", None)


class FamilyVisaExceptionForm(forms.Form):
    """HR or Management take family status away from one person, or put them back on the designation's default.
    Giving family status is not here: it comes only from an approved request (or the designation's tick)."""
    mode = forms.ChoiceField(
        label="What should change?",
        choices=[(FamilyVisaException.Mode.NOT_ALLOWED, "Remove family status"),
                 (FamilyVisaException.Mode.DEFAULT, "Follow the designation again (clears this decision)")])
    reason = forms.CharField(
        required=False, label="Reason", widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Needed when removing family status. It is kept with the change.")

    def __init__(self, *args, employee, **kwargs):
        super().__init__(*args, **kwargs)
        row = FamilyVisaException.objects.filter(employee=employee).first()
        if row and row.mode == FamilyVisaException.Mode.NOT_ALLOWED:
            self.initial.setdefault("mode", FamilyVisaException.Mode.DEFAULT)
            self.initial.setdefault("reason", row.reason)

    def clean(self):
        cd = super().clean()
        reason = " ".join((cd.get("reason") or "").split())
        if cd.get("mode") == FamilyVisaException.Mode.NOT_ALLOWED and not reason:
            self.add_error("reason", "Give the reason.")
        cd["reason"] = reason
        return cd
