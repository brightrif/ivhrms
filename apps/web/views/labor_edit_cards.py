"""Edit a labor worker one card at a time, or everything at once."""
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse

from apps.employees.models import Employee
from apps.labor import corrections
from apps.labor.models import LaborProfile
from apps.labor.services import LaborError
from apps.web.access import hr_perm
from apps.web.forms.labor import TERM_FIELDS, CorrectPayForm, LaborFullEditForm, WorkerDetailsForm
from apps.web.views.worker_type import _page, _problems

WORKER_CARD = {"title": "Worker", "width": "col-lg-4",
               "rows": [["first_name", "last_name"], ["nationality"], ["phone"], ["joining_date"]]}
ENGAGEMENT_CARD = {"title": "Engagement", "width": "col-lg-4",
                   "rows": [["engagement"], ["contractor"], ["trade"], ["notes"]]}
PAY_CARD = {"title": "Pay", "width": "col-lg-4",
            "rows": [["wage_basis", "rate"]] + [[n] for n in TERM_FIELDS if n not in ("wage_basis", "rate")]}


def _profile(request, pk):
    qs = LaborProfile.objects.for_user(request.user).select_related("employee", "company", "trade")
    return get_object_or_404(qs, pk=pk)


def _open(request, profile):
    """(back url, a redirect if this worker cannot be edited here)."""
    back = reverse("web:labor_detail", args=[profile.pk])
    emp = profile.employee
    if emp.status == Employee.Status.SEPARATED or emp.worker_type != Employee.WorkerType.LABOR:
        messages.error(request, "Only a labor worker who is still with the company can be edited here.")
        return back, redirect(back)
    return back, None


def _worker_values(cd):
    return {k: cd[k] for k in ("first_name", "last_name", "nationality", "phone")}


@hr_perm("employees.change_employee")
@hr_perm("labor.change_laborprofile")
def labor_worker_edit(request, pk):
    profile = _profile(request, pk)
    back, blocked = _open(request, profile)
    if blocked:
        return blocked
    emp = profile.employee
    form = WorkerDetailsForm(request.POST or None, employee=emp)
    if request.method == "POST" and form.is_valid():
        cd = form.cleaned_data
        try:
            notes = corrections.edit_worker(emp, joining_date=cd["joining_date"], **_worker_values(cd))
        except LaborError as exc:
            form.add_error(None, str(exc))
            messages.error(request, str(exc))
        else:
            messages.success(request, "Worker details updated.")
            for note in notes:
                messages.warning(request, note)
            return redirect(back)
    elif request.method == "POST":
        messages.error(request, "Nothing was saved. " + _problems(form))
    return _page(request, form, f"Edit worker: {emp.full_name}", back, [{**WORKER_CARD, "width": "col-lg-5"}],
                 f"{emp.employee_no}. Name, nationality, phone and joining date.", "Save")


@hr_perm("labor.add_laborrate")
def labor_pay_correct(request, pk):
    profile = _profile(request, pk)
    back, blocked = _open(request, profile)
    if blocked:
        return blocked
    current = profile.current_rate
    if current is None:
        messages.error(request, "There are no current pay terms to correct. Use Change pay.")
        return redirect(back)
    form = CorrectPayForm(request.POST or None, profile=profile)
    if request.method == "POST" and form.is_valid():
        try:
            corrections.correct_rate(profile, terms={n: form.cleaned_data[n] for n in TERM_FIELDS})
        except LaborError as exc:
            form.add_error(None, str(exc))
            messages.error(request, str(exc))
        else:
            messages.success(request, "The current pay was corrected.")
            return redirect(back)
    elif request.method == "POST":
        messages.error(request, "Nothing was saved. " + _problems(form))
    intro = (f"Fix a mistake in the pay that started on {current.effective_from:%d %b %Y}. "
             "It changes that line in place and adds nothing to the history. "
             "For new pay from a later date, use Change pay instead.")
    return _page(request, form, f"Correct pay: {profile.employee.full_name}", back,
                 [{**PAY_CARD, "title": "Current pay", "width": "col-lg-5"}], intro, "Save correction")


@hr_perm("employees.change_employee")
@hr_perm("labor.change_laborprofile")
@hr_perm("labor.add_laborrate")
def labor_full_edit(request, pk):
    profile = _profile(request, pk)
    back, blocked = _open(request, profile)
    if blocked:
        return blocked
    emp = profile.employee
    form = LaborFullEditForm(request.POST or None, employee=emp, profile=profile, user=request.user)
    if request.method == "POST" and form.is_valid():
        cd = form.cleaned_data
        try:
            notes = corrections.edit_everything(
                profile, worker=_worker_values(cd), joining_date=cd["joining_date"], engagement=cd["engagement"],
                contractor=cd["contractor"], trade=cd["trade"], notes=cd["notes"],
                terms={n: cd[n] for n in TERM_FIELDS if n in cd})
        except LaborError as exc:
            form.add_error(None, str(exc))
            messages.error(request, str(exc))
        else:
            messages.success(request, "Details updated.")
            for note in notes:
                messages.warning(request, note)
            return redirect(back)
    elif request.method == "POST":
        messages.error(request, "Nothing was saved. " + _problems(form))
    intro = ("Everything on this page in one go. The pay boxes correct the current pay line in place; "
             "for new pay from a later date, use Change pay.")
    return _page(request, form, f"Edit: {emp.full_name}", back, [WORKER_CARD, ENGAGEMENT_CARD, PAY_CARD], intro, "Save")