"""Where workers are deployed. Views and forms call these; they never change the allocation tables directly."""
from datetime import timedelta

from django.db import transaction
from django.db.models import Count, Max, Q

from apps.employees.models import Employee
from apps.organization.models import Project
from apps.organization.services import companies_for

from .allocation import LaborAllocation, WorkOrder
from .models import LaborProfile
from .services import LaborError


def _label(employee):
    return f"{employee.employee_no} {employee.full_name}"


def open_allocation(employee):
    """The worker's main project right now. For a worker on one project, the only one."""
    return next(iter(open_allocations(employee)), None)


def _same_place(allocation, project, location, work_order):
    return (allocation.project_id, allocation.location_id, allocation.work_order_id) == (
        project.pk, location.pk, work_order.pk if work_order else None)


def _check_destination(employee, project, location, work_order, effective_from):
    if not LaborProfile.objects.filter(employee=employee).exists():
        raise LaborError("This worker has no labor profile yet.")
    if employee.status == Employee.Status.SEPARATED:
        raise LaborError("This worker has left the company.")
    if project.company_id != employee.company_id:
        raise LaborError(f"{project.code} belongs to another company.")
    if project.status != Project.Status.ACTIVE:
        raise LaborError(f"{project.code} is not active.")
    if not (location.is_site and location.is_active):
        raise LaborError(f"{location.name} is not an active work site. Mark it as a site under Locations first.")
    if work_order is not None:
        if work_order.project_id != project.pk:
            raise LaborError(f"Work order {work_order.code} belongs to another project.")
        if work_order.status != WorkOrder.Status.OPEN:
            raise LaborError(f"Work order {work_order.code} is closed.")
    if effective_from < employee.joining_date:
        raise LaborError(f"{employee.full_name} joined on {employee.joining_date:%d %b %Y}; "
                         "the allocation cannot start earlier.")


@transaction.atomic
def allocate(employee, *, project, location, work_order=None, effective_from, notes=""):
    """Move a worker who is on one project (or none) to a project and site from a date. Where they were ends the
    day before. To put a worker on one more project without moving them, use join_project."""
    _check_destination(employee, project, location, work_order, effective_from)
    current = _the_only_open(employee, lock=True)
    if current:
        if _same_place(current, project, location, work_order):
            raise LaborError("This worker is already allocated there.")
        if effective_from <= current.effective_from:
            raise LaborError(f"The move must start after the current allocation, which began on "
                             f"{current.effective_from:%d %b %Y}.")
        current.effective_to = effective_from - timedelta(days=1)
        current.save()
    else:
        last_end = LaborAllocation.objects.filter(employee=employee).aggregate(last=Max("effective_to"))["last"]
        if last_end and effective_from <= last_end:
            raise LaborError(f"The previous allocation ran until {last_end:%d %b %Y}; "
                             "the new one must start after that.")
    return LaborAllocation.objects.create(employee=employee, project=project, location=location,
                                          work_order=work_order, effective_from=effective_from,
                                          notes=notes or "", is_main=True)


@transaction.atomic
def release(employee, *, last_day):
    """Take a worker off their site (finished, demobilised, idle). A worker on several projects leaves them one at
    a time (leave_project)."""
    current = _the_only_open(employee, lock=True)
    if current is None:
        raise LaborError("This worker is not allocated anywhere.")
    if last_day < current.effective_from:
        raise LaborError(f"The last day cannot be before the allocation started on {current.effective_from:%d %b %Y}.")
    current.effective_to = last_day
    current.save()
    return current


@transaction.atomic
def transfer(employees, *, project, location, work_order=None, effective_from):
    """Move a crew in one go. All or nothing: if any worker cannot move, nobody is moved and the reasons are listed.
    Workers already at the destination are left alone. Returns (moved, already_there)."""
    errors, todo, already = [], [], 0
    for employee in employees:
        try:
            current = _the_only_open(employee)
            if current and _same_place(current, project, location, work_order):
                already += 1
                continue
            _check_destination(employee, project, location, work_order, effective_from)
            if current and effective_from <= current.effective_from:
                raise LaborError(f"already there since {current.effective_from:%d %b %Y}; pick a later date")
            if current is None:
                last_end = LaborAllocation.objects.filter(employee=employee).aggregate(last=Max("effective_to"))["last"]
                if last_end and effective_from <= last_end:
                    raise LaborError(f"the previous allocation ran until {last_end:%d %b %Y}; pick a later date")
        except LaborError as exc:
            errors.append(f"{_label(employee)}: {exc}")
        else:
            todo.append(employee)
    if errors:
        more = f" (and {len(errors) - 5} more)" if len(errors) > 5 else ""
        raise LaborError("Nobody was moved. " + " | ".join(errors[:5]) + more)
    for employee in todo:
        allocate(employee, project=project, location=location, work_order=work_order, effective_from=effective_from)
    return len(todo), already


def allocation_on(employee, on_date):
    """Where the worker was on a date, or None. On a day with several projects, the main one. Attendance and
    timesheets use this to default the site."""
    return (LaborAllocation.objects.filter(employee=employee, effective_from__lte=on_date)
            .exclude(effective_to__lt=on_date).order_by("-is_main", "-effective_from")
            .select_related("project", "location", "work_order").first())


def unallocated_profiles(user):
    """Labor workers, still with the company, who are on no project right now. Shared workers serve them all."""
    placed = LaborAllocation.objects.filter(effective_to__isnull=True).values("employee_id")
    return (LaborProfile.objects.for_user(user).exclude(employee__status=Employee.Status.SEPARATED)
            .exclude(serves_all_projects=True).exclude(employee_id__in=placed))


def site_overview(user):
    """One row per project and site: how many workers are there now. Active projects with nobody show as 0."""
    live = (LaborAllocation.objects.for_user(user).filter(effective_to__isnull=True)
            .exclude(employee__status=Employee.Status.SEPARATED)
            .values("project_id", "location_id")
            .annotate(total=Count("id"),
                      direct=Count("id", filter=Q(employee__labor_profile__engagement="direct")),
                      contracted=Count("id", filter=Q(employee__labor_profile__engagement="contracted"))))
    counts = {(r["project_id"], r["location_id"]): r for r in live}
    projects = {p.pk: p for p in Project.objects.filter(company__in=companies_for(user))
                .select_related("company", "location")}
    from apps.organization.models import Location
    sites = {l.pk: l for l in Location.objects.all()}
    rows = []
    for (project_id, location_id), r in counts.items():
        if project_id in projects and location_id in sites:
            rows.append({"project": projects[project_id], "location": sites[location_id], **{k: r[k] for k in ("total", "direct", "contracted")}})
    seen = {r["project"].pk for r in rows}
    for p in projects.values():
        if p.pk not in seen and p.status == Project.Status.ACTIVE and p.location_id:
            rows.append({"project": p, "location": p.location, "total": 0, "direct": 0, "contracted": 0})
    rows.sort(key=lambda r: (r["project"].code, r["location"].name))
    return rows


@transaction.atomic
def set_work_order_open(work_order, is_open):
    """Close a work order (no new allocations) or reopen it. It cannot be closed while workers are still on it."""
    work_order = WorkOrder.objects.select_for_update().get(pk=work_order.pk)
    if not is_open:
        n = (work_order.allocations.filter(effective_to__isnull=True)
             .exclude(employee__status=Employee.Status.SEPARATED).count())
        if n:
            raise LaborError(f"{n} worker{'s are' if n != 1 else ' is'} still allocated to {work_order.code}. "
                             "Move them first.")
    work_order.status = WorkOrder.Status.OPEN if is_open else WorkOrder.Status.CLOSED
    work_order.save(update_fields=["status"])
    return work_order


def open_allocations(employee):
    """Every project the worker is on now, the main one first."""
    return list(LaborAllocation.objects.filter(employee=employee, effective_to__isnull=True)
                .select_related("project", "location", "work_order").order_by("-is_main", "effective_from", "id"))


def _the_only_open(employee, lock=False):
    """The worker's single open allocation, or None. A worker on several projects has no one place to move from."""
    qs = LaborAllocation.objects.filter(employee=employee, effective_to__isnull=True)
    if lock:
        qs = qs.select_for_update()
    rows = list(qs)
    if len(rows) > 1:
        raise LaborError(f"{employee.full_name} is on {len(rows)} projects. Add or remove projects from "
                         "each project's Team page instead.")
    return rows[0] if rows else None


def _project_site(project):
    if not project.location_id:
        raise LaborError(f"{project.code} has no site of its own. Set its site under Organization > Projects first.")
    return project.location


@transaction.atomic
def join_project(employee, *, project, location=None, work_order=None, effective_from, notes=""):
    """Put a worker on one more project without touching the others. The site is the project's own unless given."""
    location = location or _project_site(project)
    _check_destination(employee, project, location, work_order, effective_from)
    mine = list(LaborAllocation.objects.select_for_update().filter(employee=employee, project=project))
    if any(a.effective_to is None for a in mine):
        raise LaborError(f"{employee.full_name} is already on {project.code}.")
    ended = max((a.effective_to for a in mine), default=None)
    if ended and effective_from <= ended:
        raise LaborError(f"{employee.full_name} was on {project.code} until {ended:%d %b %Y}; "
                         "the new start must be after that.")
    has_open = LaborAllocation.objects.filter(employee=employee, effective_to__isnull=True).exists()
    return LaborAllocation.objects.create(employee=employee, project=project, location=location,
                                          work_order=work_order, effective_from=effective_from,
                                          notes=notes or "", is_main=not has_open)


@transaction.atomic
def leave_project(allocation, *, last_day):
    """End one project for a worker. Their other projects carry on, and one of them becomes the main."""
    allocation = LaborAllocation.objects.select_for_update().get(pk=allocation.pk)
    if allocation.effective_to is not None:
        raise LaborError("This worker has already left that project.")
    if last_day < allocation.effective_from:
        raise LaborError(f"The last day cannot be before the allocation started on {allocation.effective_from:%d %b %Y}.")
    allocation.effective_to = last_day
    allocation.save()
    if allocation.is_main:
        nxt = (LaborAllocation.objects.select_for_update()
               .filter(employee_id=allocation.employee_id, effective_to__isnull=True)
               .order_by("effective_from", "id").first())
        if nxt:
            nxt.is_main = True
            nxt.save(update_fields=["is_main"])
    return allocation


@transaction.atomic
def set_main(allocation):
    """Make this the worker's main project: where attendance and hours start from."""
    allocation = LaborAllocation.objects.select_for_update().get(pk=allocation.pk)
    if allocation.effective_to is not None:
        raise LaborError("That project has ended for this worker.")
    for other in (LaborAllocation.objects.select_for_update()
                  .filter(employee_id=allocation.employee_id, effective_to__isnull=True, is_main=True)
                  .exclude(pk=allocation.pk)):
        other.is_main = False
        other.save(update_fields=["is_main"])
    allocation.is_main = True
    allocation.save(update_fields=["is_main"])
    return allocation


@transaction.atomic
def add_workers(employees, *, project, effective_from):
    """Put several workers on a project in one go. Anyone already on it is left alone. All or nothing: if any
    worker cannot be added, nobody is, and the reasons are listed. Returns (added, already_there)."""
    location = _project_site(project)
    errors, todo, already = [], [], 0
    for employee in employees:
        if LaborAllocation.objects.filter(employee=employee, project=project, effective_to__isnull=True).exists():
            already += 1
            continue
        try:
            _check_destination(employee, project, location, None, effective_from)
            ended = (LaborAllocation.objects.filter(employee=employee, project=project)
                     .aggregate(last=Max("effective_to"))["last"])
            if ended and effective_from <= ended:
                raise LaborError(f"was on {project.code} until {ended:%d %b %Y}; pick a later date")
        except LaborError as exc:
            errors.append(f"{_label(employee)}: {exc}")
        else:
            todo.append(employee)
    if errors:
        more = f" (and {len(errors) - 5} more)" if len(errors) > 5 else ""
        raise LaborError("Nobody was added. " + " | ".join(errors[:5]) + more)
    for employee in todo:
        join_project(employee, project=project, effective_from=effective_from)
    return len(todo), already


def set_shared(profile, shared):
    """Mark a worker (a driver, say) as working for every project, or stop that."""
    if profile.serves_all_projects != shared:
        profile.serves_all_projects = shared
        profile.save(update_fields=["serves_all_projects"])
    return profile


def shared_profiles(user):
    return (LaborProfile.objects.for_user(user).filter(serves_all_projects=True)
            .exclude(employee__status=Employee.Status.SEPARATED).select_related("employee", "trade")
            .order_by("employee__employee_no"))


def headcount(user):
    """Distinct workers on any project now. A worker on three projects counts once."""
    people = (LaborAllocation.objects.for_user(user).filter(effective_to__isnull=True)
              .exclude(employee__status=Employee.Status.SEPARATED).order_by()      # the default ordering would split DISTINCT
              .values("employee_id", "employee__labor_profile__engagement").distinct())
    total = direct = 0
    for p in people:
        total += 1
        direct += p["employee__labor_profile__engagement"] == "direct"
    return {"total": total, "direct": direct, "contracted": total - direct}
