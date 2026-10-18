"""Family members are added for a known employee who has family status (from that employee's page, or the
search-first picker), and HR or Management can take family status away again."""
from django.contrib import messages
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.cache import patch_vary_headers
from django.utils.http import url_has_allowed_host_and_scheme

from apps.dependants import eligibility, services
from apps.employees.models import Employee
from apps.organization.services import companies_for

from apps.web.access import hr_perm
from apps.web.forms.family_eligibility import FamilyVisaExceptionForm, SponsoredDependantForm
from apps.web.views.family import LAYOUTS as FAMILY_LAYOUTS
from apps.web.views.family import _detail

PICK_LIMIT = 10
VISA_LAYOUT = [{"title": "Family status", "width": "col-lg-6", "rows": [["mode"], ["reason"]]}]


def _form_page(request, form, title, back_url, layout, intro="", submit_label="Save"):
    return render(request, "web/form_layout.html", {
        "form": form, "title": title, "back_url": back_url, "layout": layout, "intro": intro,
        "submit_label": submit_label})


def _back(request, default):
    """Where to return to: the page the person came from, if it is on this site."""
    url = request.GET.get("next") or request.POST.get("next") or ""
    safe = url and url_has_allowed_host_and_scheme(
        url, allowed_hosts={request.get_host()}, require_https=request.is_secure())
    return url if safe else default


# ------------------------------------------------------------------ find the sponsor first

@hr_perm("dependants.add_dependant")
def family_pick(request):
    """Search first: nothing is listed until at least 2 letters are typed, and never more than the best matches."""
    q = " ".join(request.GET.get("q", "").split())
    words = q.split()
    rows, hint, more = [], "", False
    if not any(len(w) >= 2 for w in words):
        hint = "Type at least 2 letters of the name, or part of the employee number."
    else:
        qs = Employee.objects.for_user(request.user).exclude(status=Employee.Status.SEPARATED)
        for word in words:
            qs = qs.filter(Q(first_name__icontains=word) | Q(last_name__icontains=word)
                           | Q(employee_no__icontains=word))
        rows = list(qs.select_related("company", "designation").order_by("employee_no")[:PICK_LIMIT + 1])
        more, rows = len(rows) > PICK_LIMIT, rows[:PICK_LIMIT]
        for e in rows:
            e.family_visa = eligibility.sponsor_status(e)
        if not rows:
            hint = "Nobody matches that."
    ctx = {"q": q, "rows": rows, "hint": hint, "more": more, "limit": PICK_LIMIT,
           "multi_company": companies_for(request.user).count() > 1}
    partial = request.headers.get("HX-Request") == "true" and not request.headers.get("HX-History-Restore-Request")
    resp = render(request, "web/family/_pick_results.html" if partial else "web/family/pick.html", ctx)
    patch_vary_headers(resp, ["HX-Request"])
    return resp


# ------------------------------------------------------------------ add a family member

def _sponsor(request, raw):
    if not str(raw or "").isdigit():
        return None
    return (Employee.objects.for_user(request.user).exclude(status=Employee.Status.SEPARATED)
            .select_related("company", "designation").filter(pk=int(raw)).first())


@hr_perm("dependants.add_dependant")
def family_add(request):
    """The employee is always known here: from their own page, or from the picker. There is no list of everyone."""
    raw = request.GET.get("employee") or request.POST.get("employee") or ""
    sponsor = _sponsor(request, raw)
    if sponsor is None:
        if raw:
            messages.error(request, "Family members cannot be added for that employee.")
        return redirect("web:employee_family_pick")
    if eligibility.sponsor_problem(sponsor):
        messages.error(request, f"{sponsor.full_name} needs family status before family details can be recorded.")
        if request.user.has_perm("dependants.add_familystatusrequest"):
            return redirect("web:employee_family_status_add", pk=sponsor.pk)
        return redirect("web:employee_family_pick")
    back = _back(request, reverse("web:employee_family"))
    form = SponsoredDependantForm(request.POST or None, user=request.user)
    if request.method == "POST" and form.is_valid():
        try:
            dep = services.add_dependant(request.user, sponsor, **form.cleaned_data)
        except services.DependantError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, f"{dep.full_name} was added. Add their passport, CPR card and visa next.")
            return redirect(_detail(dep))
    intro = f"Sponsor: {sponsor.full_name} ({sponsor.employee_no}). {eligibility.sponsor_status(sponsor).text}."
    return _form_page(request, form, "Add a family member", back, FAMILY_LAYOUTS["dependant_edit"], intro)


# ------------------------------------------------------------------ take family status away

@hr_perm("dependants.change_familyvisaexception")
def family_visa_edit(request, pk):
    """Remove family status from one person (with a reason), or put them back on their designation's default.
    Giving family status is only done by an approved request or the designation's tick."""
    employee = get_object_or_404(
        Employee.objects.for_user(request.user).select_related("company", "designation"), pk=pk)
    back = _back(request, reverse("web:employee_detail", args=[employee.pk]))
    if employee.status == Employee.Status.SEPARATED:
        messages.error(request, f"{employee.full_name} has left the company.")
        return redirect(back)
    status = eligibility.sponsor_status(employee)
    form = FamilyVisaExceptionForm(request.POST or None, employee=employee)
    if request.method == "POST" and form.is_valid():
        try:
            eligibility.set_exception(employee, form.cleaned_data["mode"], form.cleaned_data["reason"])
        except eligibility.EligibilityError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "The family status setting was saved.")
            return redirect(back)
    designation = employee.designation.name if employee.designation_id else "no designation"
    intro = (f"{employee.full_name} ({employee.employee_no}). Now: {status.text}. "
             f"The designation is {designation}. "
             "Family members already recorded and visa requests already made are not changed. "
             "A renewal or cancellation never asks.")
    return _form_page(request, form, f"Family status: {employee.full_name}", back, VISA_LAYOUT, intro)
