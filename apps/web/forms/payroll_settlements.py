from decimal import Decimal

from django import forms
from django.utils import timezone

from apps.employees.models import Employee
from apps.payroll.finalsettlement import FinalSettlement
from apps.payroll.services import in_payroll

from .common import date_input

YES_NO = [("", "Follow the rule for the reason"), ("yes", "Payable"), ("no", "Not payable")]


def _decision(value):
    return {"yes": True, "no": False}.get(value)


class SettlementForm(forms.Form):
    employee = forms.ModelChoiceField(queryset=Employee.objects.none(), label="Employee")
    reason = forms.ChoiceField(choices=FinalSettlement.Reason.choices, label="Why employment ends")
    last_working_day = forms.DateField(widget=date_input(), label="Last working day")
    notice_date = forms.DateField(widget=date_input(), required=False, label="Date notice was given",
                                  help_text="Needed for a resignation or an employer termination.")
    notice_required_days = forms.IntegerField(min_value=0, max_value=365, initial=30, label="Notice required (days)",
                                              help_text="30 is the legal minimum; the contract may say more.")
    notice_treatment = forms.ChoiceField(choices=FinalSettlement.NoticeTreatment.choices, initial="auto",
                                         label="If notice is short")
    leave_type = forms.ModelChoiceField(queryset=None, required=False, label="Annual leave type")
    gratuity_decision = forms.ChoiceField(choices=YES_NO, required=False, label="Gratuity",
                                          help_text="Must be decided for a dismissal without notice and for a GCC national.")
    gratuity_note = forms.CharField(max_length=255, required=False, label="Why",
                                    help_text="Required when gratuity is not payable.")

    def __init__(self, *args, user, case=None, **kwargs):
        super().__init__(*args, **kwargs)
        from apps.leave.models import LeaveType
        from apps.organization.services import companies_for

        self.case = case
        companies = companies_for(user)
        self.fields["leave_type"].queryset = LeaveType.objects.filter(company__in=companies, is_active=True, tracks_balance=True)
        if case is None:
            live = FinalSettlement.objects.exclude(status="cancelled").values_list("employee_id", flat=True)
            self.fields["employee"].queryset = (in_payroll(user, include_separated=True)
                                                .exclude(pk__in=list(live)).order_by("employee_no"))
            self.fields["employee"].label_from_instance = lambda e: f"{e.employee_no} - {e.full_name}"
            self.initial.setdefault("last_working_day", timezone.localdate())
        else:
            del self.fields["employee"]
            self.initial.update(
                reason=case.reason, last_working_day=case.last_working_day, notice_date=case.notice_date,
                notice_required_days=case.notice_required_days, notice_treatment=case.notice_treatment,
                leave_type=case.leave_type_id, gratuity_note=case.gratuity_note,
                gratuity_decision={True: "yes", False: "no"}.get(case.gratuity_decision, ""))

    def values(self):
        cd = self.cleaned_data
        return dict(reason=cd["reason"], last_working_day=cd["last_working_day"], notice_date=cd["notice_date"],
                    notice_required_days=cd["notice_required_days"], notice_treatment=cd["notice_treatment"],
                    gratuity_decision=_decision(cd["gratuity_decision"]), gratuity_note=cd["gratuity_note"])


class ItemForm(forms.Form):
    kind = forms.ChoiceField(choices=[("addition", "Owed to the employee"), ("deduction", "Taken from the employee")],
                             label="Type")
    label = forms.CharField(max_length=160, label="What for")
    amount = forms.DecimalField(min_value=Decimal("0.001"), max_digits=12, decimal_places=3, label="Amount (BHD)")


class PayForm(forms.Form):
    paid_on = forms.DateField(widget=date_input(max="today"), label="Paid on")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.initial.setdefault("paid_on", timezone.localdate())
