from django import forms

from apps.compliance.validators import validate_extension, validate_file_size
from apps.vehicles.files import VehicleFile

SCAN_ATTRS = {"accept": ".pdf,.jpg,.jpeg,.png"}          # a plain FileInput on purpose: these files have no public address
SCAN_HELP = "PDF, JPG or PNG, up to 10 MB."


class AttachmentForm(forms.Form):
    kind = forms.ChoiceField(label="What is it?")
    title = forms.CharField(required=False, max_length=120, label="Title",
                            help_text="Optional, for example the garage or the page of the contract.")
    file = forms.FileField(validators=[validate_extension, validate_file_size], widget=forms.FileInput(attrs=SCAN_ATTRS),
                           help_text=SCAN_HELP)

    def __init__(self, *args, allowed, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["kind"].choices = [(k, VehicleFile.Kind(k).label) for k in allowed]
