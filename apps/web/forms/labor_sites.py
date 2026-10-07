from django import forms
from django.utils import timezone

from apps.labor.allocation import WorkOrder
from apps.labor.models import LaborProfile
from apps.organization.models import Location, Project
from apps.organization.services import companies_for

from apps.web.forms.common import date_input


def _work_order_label(w):
    return f"{w.project.code} / {w.code} {w.name}"


class _PlaceForm(forms.Form):
    """A project, a site and (optionally) a work order. Used for one worker and for a whole crew."""
    project = forms.ModelChoiceField(queryset=Project.objects.none(), empty_label="Choose a project")
    location = forms.ModelChoiceField(
        queryset=Location.objects.none(), required=False, label="Site", empty_label="The project's own site",
        help_text="Leave as it is to use the site set on the project.")
    work_order = forms.ModelChoiceField(
        queryset=WorkOrder.objects.none(), required=False, empty_label="No work order",
        help_text="Only if the cost is charged to a work order or contract.")
    effective_from = forms.DateField(widget=date_input(), label="Starts on")

    def _set_place_choices(self, companies):
        self.fields["project"].queryset = (Project.objects.filter(company__in=companies, status=Project.Status.ACTIVE)
                                           .select_related("company", "location").order_by("code"))
        self.fields["location"].queryset = Location.objects.filter(is_site=True, is_active=True).order_by("name")
        wo = self.fields["work_order"]
        wo.queryset = (WorkOrder.objects.filter(project__company__in=companies, status=WorkOrder.Status.OPEN)
                       .select_related("project").order_by("project__code", "code"))
        wo.label_from_instance = _work_order_label
        self.initial.setdefault("effective_from", timezone.localdate())

    def clean(self):
        cd = super().clean()
        project, location, work_order = cd.get("project"), cd.get("location"), cd.get("work_order")
        if project:
            if location is None and project.location_id:
                cd["location"] = project.location
            elif location is None:
                self.add_error("location", f"{project.code} has no site of its own. Choose the site.")
            if work_order and work_order.project_id != project.pk:
                self.add_error("work_order", "This work order belongs to another project.")
        return cd


class AllocationForm(_PlaceForm):
    notes = forms.CharField(required=False, max_length=255)

    def __init__(self, *args, employee, user, **kwargs):
        super().__init__(*args, **kwargs)
        self._set_place_choices(companies_for(user).filter(pk=employee.company_id))
        self.fields["effective_from"].widget.attrs["data-min"] = employee.joining_date.isoformat()


class TransferForm(_PlaceForm):
    profiles = forms.ModelMultipleChoiceField(queryset=LaborProfile.objects.none(), widget=forms.MultipleHiddenInput)

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["profiles"].queryset = (LaborProfile.objects.for_user(user).select_related("employee", "company"))
        self._set_place_choices(companies_for(user))


class ReleaseForm(forms.Form):
    last_day = forms.DateField(widget=date_input(), label="Last day on site")

    def __init__(self, *args, allocation, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["last_day"].widget.attrs["data-min"] = allocation.effective_from.isoformat()
        self.initial.setdefault("last_day", max(timezone.localdate(), allocation.effective_from))


class WorkOrderForm(forms.ModelForm):
    class Meta:
        model = WorkOrder
        fields = ["project", "code", "name", "start_date", "end_date"]
        widgets = {"start_date": date_input(), "end_date": date_input()}

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        project = self.fields["project"]
        if self.instance.pk:                       # a work order stays with its project
            project.queryset = Project.objects.filter(pk=self.instance.project_id)
            project.disabled = True
        else:
            project.queryset = (Project.objects.filter(company__in=companies_for(user))
                                .exclude(status=Project.Status.CLOSED).select_related("company").order_by("code"))
            project.empty_label = "Choose a project"

    def clean_code(self):
        return self.cleaned_data["code"].strip().upper()

    def clean(self):
        cd = super().clean()
        start, end = cd.get("start_date"), cd.get("end_date")
        if start and end and end < start:
            self.add_error("end_date", "The end date cannot be before the start date.")
        return cd
