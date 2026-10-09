"""Adding workers to projects. A worker can be on any number of projects, so adding never ends another one."""
from django import forms
from django.utils import timezone

from apps.labor.allocation import LaborAllocation, WorkOrder
from apps.labor.models import LaborProfile
from apps.organization.models import Project
from apps.organization.services import companies_for

from apps.web.forms.common import date_input


def _work_order_label(w):
    return f"{w.project.code} / {w.code} {w.name}"


class AddProjectForm(forms.Form):
    """One worker, one more project. Only the project and the start date are needed: the site is the project's own."""
    project = forms.ModelChoiceField(queryset=Project.objects.none(), empty_label="Choose a project")
    effective_from = forms.DateField(widget=date_input(), label="Starts on")
    work_order = forms.ModelChoiceField(
        queryset=WorkOrder.objects.none(), required=False, empty_label="No work order",
        help_text="Only if the cost is charged to a work order or contract.")

    def __init__(self, *args, employee, user, **kwargs):
        super().__init__(*args, **kwargs)
        already = LaborAllocation.objects.filter(employee=employee, effective_to__isnull=True).values("project_id")
        active = (Project.objects.filter(company__in=companies_for(user).filter(pk=employee.company_id),
                                         status=Project.Status.ACTIVE).exclude(pk__in=already))
        self.without_site = list(active.filter(location__isnull=True).order_by("code"))
        self.fields["project"].queryset = active.filter(location__isnull=False).select_related("company", "location").order_by("code")
        wo = self.fields["work_order"]
        wo.queryset = (WorkOrder.objects.filter(project__in=self.fields["project"].queryset, status=WorkOrder.Status.OPEN)
                       .select_related("project").order_by("project__code", "code"))
        wo.label_from_instance = _work_order_label
        self.initial.setdefault("effective_from", timezone.localdate())
        self.fields["effective_from"].widget.attrs["data-min"] = employee.joining_date.isoformat()

    def clean(self):
        cd = super().clean()
        project, work_order = cd.get("project"), cd.get("work_order")
        if project and work_order and work_order.project_id != project.pk:
            self.add_error("work_order", "This work order belongs to another project.")
        return cd


class AddCrewForm(forms.Form):
    """Several ticked workers, one more project for each."""
    project = forms.ModelChoiceField(queryset=Project.objects.none(), empty_label="Choose a project")
    effective_from = forms.DateField(widget=date_input(), label="Starts on")
    profiles = forms.ModelMultipleChoiceField(queryset=LaborProfile.objects.none(), widget=forms.MultipleHiddenInput)

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["project"].queryset = (Project.objects.filter(company__in=companies_for(user), status=Project.Status.ACTIVE,
                                                                  location__isnull=False)
                                           .select_related("company", "location").order_by("code"))
        self.fields["profiles"].queryset = LaborProfile.objects.for_user(user).select_related("employee", "company")
        self.initial.setdefault("effective_from", timezone.localdate())
