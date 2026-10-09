"""The month-end tool for hours: divide a worker's days between projects, many days at once."""
from datetime import date
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from apps.employees.models import Employee
from apps.labor import hours_split
from apps.labor.allocation import LaborAllocation
from apps.labor.models import LaborProfile
from apps.labor.services import LaborError
from apps.organization.models import Project

from apps.web.access import hr_perm

_NAME_FIELDS = [f for f in ("first_name", "middle_name", "last_name")
                if f in {x.name for x in Employee._meta.get_fields()}]


def _day(raw):
    try:
        return date.fromisoformat(raw or "")
    except ValueError:
        return None


def _hours(raw):
    raw = (raw or "").strip()
    if not raw:
        return Decimal("0")
    try:
        value = Decimal(raw)
    except InvalidOperation:
        raise LaborError(f"'{raw}' is not a number of hours.") from None
    if not value.is_finite() or value < 0 or value != value.quantize(Decimal("0.01")):
        raise LaborError(f"'{raw}': enter hours as a number such as 4 or 2.5.")
    return value


def _projects_for(profile, first, last):
    """The projects this worker can be divided between: those he is on in the period, or every active project of his
    company when he serves all of them."""
    on = (LaborAllocation.objects.filter(employee=profile.employee, effective_from__lte=last)
          .filter(Q(effective_to__isnull=True) | Q(effective_to__gte=first)).values("project_id"))
    cond = Q(pk__in=on)
    if profile.serves_all_projects:
        cond |= Q(company_id=profile.company_id, status=Project.Status.ACTIVE, location__isnull=False)
    return list(Project.objects.filter(cond).select_related("location").order_by("code"))


def _people(user, q):
    people = (LaborProfile.objects.for_user(user).exclude(employee__status=Employee.Status.SEPARATED)
              .select_related("employee", "trade")
              .annotate(open_projects=Count("employee__labor_allocations",
                                            filter=Q(employee__labor_allocations__effective_to__isnull=True),
                                            distinct=True))
              .filter(Q(serves_all_projects=True) | Q(open_projects__gte=2)).order_by("employee__employee_no"))
    if q:
        cond = Q(employee__employee_no__icontains=q) | Q(trade__name__icontains=q)
        for field in _NAME_FIELDS:
            cond |= Q(**{f"employee__{field}__icontains": q})
        people = people.filter(cond)
    return list(people[:60])


@hr_perm("labor.view_timeentry")
def split(request):
    today = timezone.localdate()
    data = request.POST if request.method == "POST" else request.GET
    first = _day(data.get("first")) or today.replace(day=1)
    last = min(_day(data.get("last")) or today, today)
    profile = None
    if str(data.get("worker", "")).isdigit():
        profile = get_object_or_404(LaborProfile.objects.for_user(request.user).select_related("employee", "trade"),
                                    pk=int(data["worker"]))

    if request.method == "POST":
        if not request.user.has_perm("labor.add_timeentry"):
            raise PermissionDenied
        if profile is None:
            raise Http404
        here = {"worker": profile.pk, "first": first.isoformat(), "last": last.isoformat()}
        try:
            lines = [{"project": p, "regular": _hours(data.get(f"reg_{p.pk}")), "overtime": _hours(data.get(f"ot_{p.pk}"))}
                     for p in _projects_for(profile, first, last) if data.get(f"inc_{p.pk}")]
            result = hours_split.split_days(request.user, profile.employee, lines, first, last,
                                            even=data.get("mode") == "even", today=today)
        except LaborError as exc:
            messages.error(request, str(exc))
        else:
            if result["days"]:
                messages.success(request, f"{profile.employee.full_name}: {result['days']} day"
                                          f"{'s' if result['days'] != 1 else ''} divided, {result['lines']} lines written.")
            else:
                messages.info(request, "No day was changed.")
            if result["skipped"]:
                messages.warning(request, "Not changed: " + " | ".join(result["skipped"][:6])
                                 + (f" (and {len(result['skipped']) - 6} more)" if len(result["skipped"]) > 6 else ""))
        return redirect(f"{reverse('web:labor_split')}?{urlencode(here)}")

    return render(request, "web/labor/split.html", {
        "profile": profile, "first": first, "last": last, "q": request.GET.get("q", "").strip(),
        "projects": _projects_for(profile, first, last) if profile else [],
        "people": [] if profile else _people(request.user, request.GET.get("q", "").strip()),
        "can_apply": request.user.has_perm("labor.add_timeentry")})
