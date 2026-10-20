import calendar
from decimal import Decimal

from django import forms
from django.utils import timezone

from apps.organization.services import companies_for
from apps.payroll import runs
from apps.payroll.payrun import PayrollAdjustment, PayrollLine, PayrollRun

from .common import date_input

MONTHS = [(i, calendar.month_name[i]) for i in range(1, 13)]
MANUAL_CATEGORIES = [(c, label) for c, label in PayrollAdjustment.Category.choices
                     if c not in (PayrollAdjustment.Category.RENEWAL, PayrollAdjustment.Category.FINE,
                                  PayrollAdjustment.Category.LOAN)]


def _company_field(form, user):
    """Offer the companies the person may use; with only one, choose it and hide the box."""
    field = form.fields["company"]
    companies = companies_for(user)
    field.queryset = companies
    ids = list(companies.values_list("pk", flat=True)[:2])
    if len(ids) == 1:
        form.initial.setdefault("company", ids[0])
        field.widget = forms.HiddenInput()


class RunForm(forms.Form):
    company = forms.ModelChoiceField(queryset=None)
    kind = forms.ChoiceField(choices=PayrollRun.Kind.choices, initial="staff", label="Who is paid",
                             help_text="A direct labor run can only be made once the month is over.")
    month = forms.TypedChoiceField(choices=MONTHS, coerce=int)
    year = forms.IntegerField(min_value=2000, max_value=2100)

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        _company_field(self, user)
        today = timezone.localdate()
        previous = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
        self.initial.setdefault("month", previous[1])
        self.initial.setdefault("year", previous[0])


class AdjustmentForm(forms.Form):
    employee = forms.ModelChoiceField(queryset=None, label="Person")
    kind = forms.ChoiceField(choices=PayrollAdjustment.Kind.choices, label="Add to pay or take from it")
    category = forms.ChoiceField(choices=MANUAL_CATEGORIES, label="What for")
    label = forms.CharField(max_length=120, label="Description")
    amount = forms.DecimalField(min_value=Decimal("0.001"), max_digits=12, decimal_places=3, label="Amount (BHD)")

    def __init__(self, *args, run, **kwargs):
        super().__init__(*args, **kwargs)
        from apps.employees.models import Employee
        people = Employee.objects.filter(pk__in=run.lines.values_list("employee_id", flat=True))
        self.fields["employee"].queryset = people.order_by("employee_no")
        self.fields["employee"].label_from_instance = lambda e: f"{e.employee_no} - {e.full_name}"


class PayForm(forms.Form):
    lines = forms.ModelMultipleChoiceField(queryset=PayrollLine.objects.none(), widget=forms.CheckboxSelectMultiple,
                                           label="Who was paid")
    paid_on = forms.DateField(widget=date_input(max="today"), label="Paid on")

    def __init__(self, *args, run, method, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["lines"].queryset = runs.unpaid_lines(run, method)
        self.fields["lines"].label_from_instance = lambda l: f"{l.employee_no} {l.employee_name}: {l.net:.3f} BHD"
        self.initial.setdefault("paid_on", timezone.localdate())
        self.initial.setdefault("lines", list(self.fields["lines"].queryset.values_list("pk", flat=True)))


class PolicyForm(forms.Form):
    company = forms.ModelChoiceField(queryset=None)
    effective_from = forms.DateField(widget=date_input(), label="Starts on")
    days_in_month = forms.IntegerField(min_value=20, max_value=31, initial=30, label="Days in a month",
                                       help_text="A day's pay for absence or unpaid leave is the monthly pay divided by this.")
    sio_employee_rate = forms.DecimalField(required=False, min_value=0, max_value=100, max_digits=5, decimal_places=2,
                                           label="SIO: employee rate for Bahrainis (%)")
    sio_employer_rate = forms.DecimalField(required=False, min_value=0, max_value=100, max_digits=5, decimal_places=2,
                                           label="SIO: employer rate for Bahrainis (%)")
    sio_employer_rate_other = forms.DecimalField(required=False, min_value=0, max_value=100, max_digits=5,
                                                 decimal_places=2, label="SIO: employer rate for others (%)",
                                                 help_text="For staff who are not Bahraini. Leave empty if none.")
    sio_wage_floor = forms.DecimalField(required=False, min_value=0, max_digits=10, decimal_places=3,
                                        label="SIO: lowest wage counted (BHD)")
    sio_wage_cap = forms.DecimalField(required=False, min_value=0, max_digits=10, decimal_places=3,
                                      label="SIO: highest wage counted (BHD)")
    sio_on_paid_days = forms.BooleanField(required=False, label="SIO follows the days paid",
                                          help_text="Tick if unpaid days also reduce the SIO wage. Ask the accountant.")

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        _company_field(self, user)
        self.initial.setdefault("effective_from", timezone.localdate().replace(day=1))
