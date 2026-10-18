from django import forms

from apps.compliance.models import Document, DocumentType
from apps.compliance.validators import validate_extension, validate_file_size
from apps.dependants.models import Dependant, DependantAttachment
from apps.employees.models import Employee

from apps.web.forms.common import date_input
from apps.web.forms.compliance import SCAN_ATTRS, SCAN_HELP, _employee_label, _people_field
from apps.web.forms.compliance import _staff_field,default_responsible

class DependantForm(forms.ModelForm):
    """Add or edit a family member. The sponsor is chosen when adding and cannot be changed afterwards."""

    class Meta:
        model = Dependant
        fields = ["employee", "relationship", "first_name", "last_name", "name_ar", "gender",
                  "date_of_birth", "nationality", "notes"]
        widgets = {"date_of_birth": date_input(max="today"), "notes": forms.Textarea(attrs={"rows": 3})}
        labels = {"employee": "Sponsor (employee)", "name_ar": "Name (Arabic)"}
        help_texts = {"date_of_birth": "Required for a child."}

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            del self.fields["employee"]
            return
        employee = self.fields["employee"]
        employee.queryset = (Employee.objects.for_user(user).exclude(status=Employee.Status.SEPARATED)
                             .select_related("company").order_by("company__name", "employee_no"))
        employee.label_from_instance = _employee_label
        employee.empty_label = "Choose an employee"


class FamilyDocumentForm(forms.ModelForm):
    """A passport, CPR card or visa of one family member. Types they already hold a current one of are not offered:
    that one is renewed instead."""

    class Meta:
        model = Document
        fields = ["document_type", "number", "issue_date", "expiry_date", "responsible", "agent_name", "file",
                  "notes"]
        widgets = {"issue_date": date_input(max="today"), "expiry_date": date_input(min_from="id_issue_date"),
                   "file": forms.FileInput(attrs=SCAN_ATTRS), "notes": forms.Textarea(attrs={"rows": 2})}
        labels = {"document_type": "Document", "number": "Document number", "file": "Scanned copy",
                  "agent_name": "Handled by (agent / PRO)"}
        help_texts = {"file": SCAN_HELP}

    def __init__(self, *args, user, dependant, **kwargs):
        super().__init__(*args, **kwargs)
        self.dependant = dependant
        self.instance.dependant = dependant            # the model's own checks need to know who it is for
        held = Document.objects.filter(dependant=dependant, is_current=True).values("document_type_id")
        dtype = self.fields["document_type"]
        dtype.queryset = (DocumentType.objects.filter(is_active=True, applies_to=DocumentType.AppliesTo.DEPENDANT)
                          .exclude(pk__in=held).order_by("name"))
        dtype.empty_label = "Choose a document"
        
        default = default_responsible(user)
        if default is not None:
            self.initial.setdefault("responsible", default.pk)
        _staff_field(self, user, self.instance.responsible_id)

    def clean(self):
        cd = super().clean()
        issue, expiry = cd.get("issue_date"), cd.get("expiry_date")
        if issue and expiry and expiry < issue:
            self.add_error("expiry_date", "The expiry date cannot be before the issue date.")
        return cd


class AttachmentForm(forms.Form):
    kind = forms.ChoiceField(choices=DependantAttachment.Kind.choices, label="What is it?")
    description = forms.CharField(required=False, max_length=120,
                                  help_text="Optional, for example 'attested copy'.")
    file = forms.FileField(validators=[validate_extension, validate_file_size],
                           widget=forms.FileInput(attrs=SCAN_ATTRS), help_text=SCAN_HELP)
