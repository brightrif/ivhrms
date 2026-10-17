"""Change a person between labor and staff: a promotion, or a move to site work."""
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from apps.employees.models import Employee
from apps.labor import conversion, deployment
from apps.labor.services import LaborError
from apps.web.access import hr_perm
from apps.web.forms.labor import TERM_FIELDS, ToLaborForm
from apps.web.forms.staff import ToStaffForm

TO_STAFF_LAYOUT = [
    {"title": "Becomes staff", "width": "col-lg-6",
     "rows": [["effective_from", "employment_type"], ["designation", "department"], ["reporting_manager"]]},
    {"title": "Record", "width": "col-lg-4", "rows": [["number"], ["remarks"]]},
]
TO_LABOR_LAYOUT = [
    {"title": "Moves to labor", "width": "col-lg-4", "rows": [["effective_from"], ["number"], ["remarks"]]},
    {"title": "Engagement", "width": "col-lg-4", "rows": [["engagement"], ["contractor"], ["trade"], ["notes"]]},
    {"title": "Pay", "width": "col-lg-4",
     "rows": [["wage_basis", "rate"]] + [[n] for n in TERM_FIELDS if n not in ("wage_basis", "rate")]},
]


def _employee(request, pk):
    return get_object_or_404(Employee.objects.for_user(request.user).select_related("company"), pk=pk)


def _fit(layout, form):
    """Only the fields this form has; a card left with none is dropped."""
    cards = []
    for card in layout:
        rows = [[n for n in row if n in form.fields] for row in card["rows"]]
        rows = [r for r in rows if r]
        if rows:
            cards.append({**card, "rows": rows})
    return cards


def _page(request, form, title, back, layout, intro, submit_label):
    layout = _fit(layout, form)
    placed = {name for card in layout for row in card["rows"] for name in row}
    extra = [[name] for name in form.fields if name not in placed and not form[name].is_hidden]
    if extra:                          # a field this page does not place still shows, with its error
        layout = layout + [{"title": "More", "width": "col-lg-4", "rows": extra}]
    return render(request, "web/form_layout.html", {
        "form": form, "title": title, "back_url": back, "layout": layout, "intro": intro,
        "submit_label": submit_label})


def _problems(form):
    """What is wrong, in one line, so the message names the field."""
    parts = []
    for name, errors in form.errors.items():
        text = " ".join(errors)
        if name == "__all__":
            parts.append(text)
        else:
            label = form.fields[name].label if name in form.fields and form.fields[name].label else name.replace("_", " ")
            parts.append(f"{label}: {text}")
    return "; ".join(parts)


@hr_perm("employees.change_employee")
@hr_perm("labor.change_laborprofile")
def employee_to_staff(request, pk):
    employee = _employee(request, pk)
    back = reverse("web:employee_detail", args=[employee.pk])
    if employee.worker_type != Employee.WorkerType.LABOR or employee.status == Employee.Status.SEPARATED:
        messages.error(request, "Only a labor worker who is still with the company can be changed to staff.")
        return redirect(back)
    previous = conversion.previous_number(employee, Employee.WorkerType.STAFF)
    form = ToStaffForm(request.POST or None, employee=employee, previous=previous)
    if request.method == "POST" and form.is_valid():
        cd = form.cleaned_data
        try:
            changed = conversion.to_staff(
                employee, effective_from=cd["effective_from"], designation=cd["designation"],
                department=cd["department"], reporting_manager=cd["reporting_manager"],
                employment_type=cd["employment_type"], number=cd["number"], remarks=cd["remarks"])
        except LaborError as exc:
            form.add_error(None, str(exc))
            messages.error(request, str(exc))
        else:
            messages.success(request, f"{changed.full_name} is now a staff member ({changed.employee_no}).")
            return redirect(back)
    elif request.method == "POST":
        messages.error(request, "The change was not saved. " + _problems(form))
    intro = (f"{employee.full_name} is labor worker {employee.employee_no}. Documents, leave and history stay on the record. "
             "Their labor pay terms end the day before the date below; past site hours and overtime keep their old rates.")
    sites = deployment.open_allocations(employee)
    if sites:
        intro += " They will be taken off " + ", ".join(a.place for a in sites) + " the day before."
    return _page(request, form, f"Change to staff: {employee.full_name}", back, TO_STAFF_LAYOUT, intro,
                 "Change to staff")


@hr_perm("employees.change_employee")
@hr_perm("labor.add_laborprofile")
def employee_to_labor(request, pk):
    employee = _employee(request, pk)
    back = reverse("web:employee_detail", args=[employee.pk])
    if employee.worker_type != Employee.WorkerType.STAFF or employee.status == Employee.Status.SEPARATED:
        messages.error(request, "Only a staff member who is still with the company can be changed to labor.")
        return redirect(back)
    earlier = conversion.earlier_rate(employee)
    previous = conversion.previous_number(employee, Employee.WorkerType.LABOR)
    form = ToLaborForm(request.POST or None, employee=employee, user=request.user, previous=previous, earlier=earlier)
    if request.method == "POST" and form.is_valid():
        cd = form.cleaned_data
        try:
            changed = conversion.to_labor(
                employee, effective_from=cd["effective_from"], engagement=cd["engagement"], contractor=cd["contractor"],
                trade=cd["trade"], notes=cd["notes"], terms=None if earlier else {n: cd[n] for n in TERM_FIELDS},
                number=cd["number"], remarks=cd["remarks"])
        except LaborError as exc:
            form.add_error(None, str(exc))
            messages.error(request, str(exc))
        else:
            messages.success(request, f"{changed.full_name} is now a labor worker ({changed.employee_no}).")
            return redirect("web:labor_detail", pk=changed.labor_profile.pk)
    elif request.method == "POST":
        messages.error(request, "The change was not saved. " + _problems(form))
    intro = (f"{employee.full_name} is staff member {employee.employee_no}. Documents, leave and history stay on the record. "
             "Their department and designation are left as they are.")
    if earlier:
        intro += (f" They come back on the pay they had before ({earlier.get_wage_basis_display()}, {earlier.rate} BHD, "
                  f"{earlier.standard_hours} standard hours, and every other pay option as it was). "
                  "HR can change it afterwards under Change pay.")
    return _page(request, form, f"Change to labor: {employee.full_name}", back, TO_LABOR_LAYOUT, intro,
                 "Change to labor")