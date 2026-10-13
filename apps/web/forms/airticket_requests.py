from decimal import Decimal

from django import forms
from django.utils import timezone

from apps.airtickets import request_services as tickets
from apps.dependants.models import Dependant

from apps.web.forms.common import date_input


class FamilyChoice(forms.ModelMultipleChoiceField):
    def label_from_instance(self, d):
        extra = f", {d.age}" if d.relationship == Dependant.Relationship.CHILD and d.age is not None else ""
        return f"{d.full_name} ({d.get_relationship_display().lower()}{extra})"


class TicketRequestForm(forms.Form):
    depart_on = forms.DateField(widget=date_input(), label="Departure")
    return_on = forms.DateField(required=False, widget=date_input(min_from="id_depart_on"), label="Return",
                                help_text="Leave empty for a one-way ticket.")
    destination = forms.CharField(required=False, max_length=80, label="Flying to",
                                  help_text="For example: Cochin (COK).")
    travellers = FamilyChoice(queryset=Dependant.objects.none(), required=False, label="Family travelling",
                              widget=forms.CheckboxSelectMultiple)
    notes = forms.CharField(required=False, label="Notes for the approvers",
                            widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, employee, entitlement, **kwargs):
        super().__init__(*args, **kwargs)
        family = tickets.eligible_dependants(employee, entitlement)
        self.fields["travellers"].queryset = family
        covered = []
        if entitlement.spouse_covered:
            covered.append("a spouse")
        if entitlement.max_children:
            covered.append(f"up to {entitlement.max_children} child{'ren' if entitlement.max_children != 1 else ''}")
        self.fields["travellers"].help_text = (
            ("The policy covers " + " and ".join(covered) + ". " if covered else "The policy covers the employee only. ")
            + ("" if family else "No covered family member is on file."))
        profile = getattr(employee, "travel_profile", None)
        if profile and profile.destination_airport:
            self.initial.setdefault("destination", profile.destination_airport)


class IssueForm(forms.Form):
    """The tickets bought: who has which ticket number and what it cost. One pair of boxes per traveller."""
    airline = forms.CharField(required=False, max_length=80)
    booking_ref = forms.CharField(required=False, max_length=40, label="Booking reference")
    issued_on = forms.DateField(widget=date_input(max="today"), label="Issued on")
    employee_share = forms.DecimalField(required=False, min_value=Decimal("0"), max_digits=12, decimal_places=3,
                                        label="Paid by the employee (BHD)",
                                        help_text="Only the part of the fares the employee pays. Usually nothing.")

    def __init__(self, *args, request, **kwargs):
        super().__init__(*args, **kwargs)
        self.initial.setdefault("issued_on", timezone.localdate())
        self.travellers = list(request.travellers.all())
        for t in self.travellers:
            self.fields[f"ticket_{t.pk}"] = forms.CharField(max_length=30, label=f"Ticket number: {t.name}")
            self.fields[f"fare_{t.pk}"] = forms.DecimalField(min_value=Decimal("0"), max_digits=12, decimal_places=3,
                                                             label="Fare (BHD)")

    def tickets(self):
        cd = self.cleaned_data
        return {t.pk: (cd[f"ticket_{t.pk}"], cd[f"fare_{t.pk}"]) for t in self.travellers}


class TravelledForm(forms.Form):
    departed_on = forms.DateField(widget=date_input(max="today"), label="Departed on")
    returned_on = forms.DateField(required=False, widget=date_input(min_from="id_departed_on"), label="Returned on",
                                  help_text="Leave empty if they have not come back yet.")

    def __init__(self, *args, request, **kwargs):
        super().__init__(*args, **kwargs)
        self.initial.setdefault("departed_on", min(request.depart_on, timezone.localdate()))


class PaidForm(forms.Form):
    paid_on = forms.DateField(widget=date_input(max="today"), label="Paid on")
    amount = forms.DecimalField(min_value=Decimal("0.001"), max_digits=12, decimal_places=3, label="Amount (BHD)")
    reference = forms.CharField(required=False, max_length=60, label="Payment reference",
                                help_text="The payroll or voucher reference, if you have one.")

    def __init__(self, *args, request, **kwargs):
        super().__init__(*args, **kwargs)
        self.initial.setdefault("paid_on", timezone.localdate())
        self.initial.setdefault("amount", request.allowance_total)


class CancelForm(forms.Form):
    reason = forms.CharField(label="Reason", widget=forms.Textarea(attrs={"rows": 3}),
                             help_text="The employee can ask for a new ticket afterwards.")
