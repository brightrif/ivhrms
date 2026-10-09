"""The project team page: who is on a project, who is shared with every project, and a quick way to add people.

Hours and teams are usually sorted out at the end of the month, so every date here is shown and can be in the past:
nothing on this page assumes the change is happening today."""
from collections import defaultdict
from datetime import date
from urllib.parse import urlencode

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from apps.employees.models import Employee
from apps.labor import deployment
from apps.labor.allocation import LaborAllocation
from apps.labor.models import LaborProfile
from apps.labor.services import LaborError
from apps.organization.models import Project
from apps.organization.services import companies_for

from apps.web.access import hr_perm

LIMIT = 100
_NAME_FIELDS = [f for f in ("first_name", "middle_name", "last_name")
                if f in {x.name for x in Employee._meta.get_fields()}]


def _project(request, pk):
    qs = Project.objects.filter(company__in=companies_for(request.user)).select_related("company", "location")
    return get_object_or_404(qs, pk=pk)


def _pk(raw):
    if not str(raw or "").isdigit():
        raise Http404
    return int(raw)


def _day(raw):
    try:
        return date.fromisoformat(raw or "")
    except ValueError:
        return None


def _need(request, perm):
    if not request.user.has_perm(perm):
        raise PermissionDenied


def _back(project, q):
    url = reverse("web:labor_team", args=[project.pk])
    return f"{url}?{urlencode({'q': q})}" if q else url


def _post(request, project):
    post, q = request.POST, request.POST.get("q", "").strip()
    action = post.get("action", "")
    try:
        if "share" in post or "unshare" in post:
            _need(request, "labor.add_laborprofile")
            sharing = "share" in post
            profile = get_object_or_404(LaborProfile.objects.for_user(request.user).select_related("employee"),
                                        pk=_pk(post.get("share") or post.get("unshare")))
            deployment.set_shared(profile, sharing)
            messages.success(request, f"{profile.employee.full_name} "
                                      f"{'now serves every project' if sharing else 'is no longer shared'}.")
        elif action == "add":
            _need(request, "labor.add_laborallocation")
            start = _day(post.get("effective_from"))
            ids = [i for i in post.getlist("profiles") if i.isdigit()]
            if start is None:
                messages.error(request, "Enter the date they start on this project.")
            elif not ids:
                messages.error(request, "Tick at least one worker first.")
            else:
                people = list(LaborProfile.objects.for_user(request.user)
                              .filter(pk__in=ids, company=project.company).select_related("employee"))
                added, already = deployment.add_workers([p.employee for p in people], project=project,
                                                        effective_from=start)
                note = f" {already} {'was' if already == 1 else 'were'} already on it." if already else ""
                messages.success(request, f"{added} worker{'s' if added != 1 else ''} added to {project.code}.{note}")
        elif action in ("remove", "main"):
            _need(request, "labor.change_laborallocation")
            allocation = get_object_or_404(LaborAllocation.objects.for_user(request.user).select_related("employee"),
                                           pk=_pk(post.get("allocation")), project=project, effective_to__isnull=True)
            if action == "main":
                deployment.set_main(allocation)
                messages.success(request, f"{project.code} is now the main project of {allocation.employee.full_name}.")
            else:
                last = _day(post.get("last_day"))
                if last is None:
                    messages.error(request, "Enter the last day on this project.")
                else:
                    deployment.leave_project(allocation, last_day=last)
                    messages.success(request, f"{allocation.employee.full_name} left {project.code} "
                                              f"(last day {last:%d %b %Y}).")
    except LaborError as exc:
        messages.error(request, str(exc))
    return redirect(_back(project, q))


@hr_perm("labor.view_laborallocation")
def team(request, project_pk):
    project = _project(request, project_pk)
    if request.method == "POST":
        return _post(request, project)

    roster = list(LaborAllocation.objects.for_user(request.user)
                  .filter(project=project, effective_to__isnull=True)
                  .exclude(employee__status=Employee.Status.SEPARATED)
                  .select_related("employee", "employee__labor_profile__trade", "work_order")
                  .order_by("employee__employee_no"))
    elsewhere = defaultdict(list)
    for other in (LaborAllocation.objects.for_user(request.user)
                  .filter(employee_id__in=[a.employee_id for a in roster], effective_to__isnull=True)
                  .exclude(project=project).select_related("project").order_by("project__code")):
        elsewhere[other.employee_id].append(other.project.code)
    for a in roster:
        a.also_on = elsewhere.get(a.employee_id, [])

    q = request.GET.get("q", "").strip()
    candidates = (LaborProfile.objects.for_user(request.user).filter(company=project.company)
                  .exclude(employee__status=Employee.Status.SEPARATED).exclude(serves_all_projects=True)
                  .exclude(employee_id__in=[a.employee_id for a in roster])
                  .select_related("employee", "trade").order_by("employee__employee_no"))
    if q:
        cond = Q(employee__employee_no__icontains=q) | Q(trade__name__icontains=q)
        for field in _NAME_FIELDS:
            cond |= Q(**{f"employee__{field}__icontains": q})
        candidates = candidates.filter(cond)
    candidates = list(candidates[:LIMIT + 1])
    too_many, candidates = len(candidates) > LIMIT, candidates[:LIMIT]
    where = defaultdict(list)
    for o in (LaborAllocation.objects.filter(employee_id__in=[c.employee_id for c in candidates],
                                             effective_to__isnull=True)
              .select_related("project").order_by("project__code")):
        where[o.employee_id].append(o.project.code)
    for c in candidates:
        c.on_projects = where.get(c.employee_id, [])

    return render(request, "web/labor/team.html", {
        "project": project, "roster": roster, "q": q, "candidates": candidates, "too_many": too_many, "limit": LIMIT,
        "shared": list(deployment.shared_profiles(request.user).filter(company=project.company)),
        "today": timezone.localdate(),
        "can_add": request.user.has_perm("labor.add_laborallocation"),
        "can_change": request.user.has_perm("labor.change_laborallocation"),
        "can_share": request.user.has_perm("labor.add_laborprofile")})
