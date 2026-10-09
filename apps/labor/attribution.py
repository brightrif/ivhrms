"""Whose day it is when a worker is on more than one project.

The cost and manpower reports used to give a worker's whole day to one project: the one the attendance was marked on.
That is right for a worker on one project and wrong for anyone else, so a worker on several projects was charged to
only one of them, and (for a monthly salary) charged once for every project. This module says how a day is divided:

* One project that day (the usual case): `parts()` returns None, and the reports use their old rules unchanged.
* Hours entered on several projects: divided by the regular hours on each project's line. Overtime is not part of
  this: it is claimed, and charged, on the project that asked for it.
* No hours entered yet: divided equally between the projects the worker is on that day, and for a worker shared with
  every project, between the company's active projects. This is an estimate that firms up as hours are entered."""
from collections import defaultdict
from decimal import Decimal

from django.db.models import Q

from apps.organization.models import Project

from .allocation import LaborAllocation
from .models import LaborProfile
from .timesheet import TimeEntry

ZERO, ONE = Decimal("0"), Decimal("1")


class Attribution:
    def __init__(self, profiles, spans, first, last):
        """profiles: {employee_id: LaborProfile with its employee loaded}; spans: {employee_id: [allocations]}."""
        self.profiles, self.spans = profiles, spans
        self.entries = defaultdict(list)
        for e in (TimeEntry.objects.filter(employee_id__in=list(profiles), date__range=(first, last))
                  .select_related("project", "location", "work_order")):
            self.entries[(e.employee_id, e.date)].append(e)
        self._active = {}

    @classmethod
    def for_employees(cls, employee_ids, first, last):
        profiles = {p.employee_id: p for p in LaborProfile.objects.filter(employee_id__in=list(employee_ids))
                    .select_related("employee")}
        spans = defaultdict(list)
        for a in (LaborAllocation.objects.filter(employee_id__in=list(profiles), effective_from__lte=last)
                  .filter(Q(effective_to__isnull=True) | Q(effective_to__gte=first))
                  .select_related("project", "location", "work_order")):
            spans[a.employee_id].append(a)
        return cls(profiles, spans, first, last)

    def _active_projects(self, company_id):
        if company_id not in self._active:
            self._active[company_id] = list(Project.objects.filter(
                company_id=company_id, status=Project.Status.ACTIVE, location__isnull=False)
                .select_related("location").order_by("code"))
        return self._active[company_id]

    def parts(self, employee_id, day):
        """None when the day has one owner; otherwise [(project, location, work_order, share)], shares adding up to 1."""
        profile = self.profiles[employee_id]
        covering = [a for a in self.spans.get(employee_id, ())
                    if a.effective_from <= day and (a.effective_to is None or day <= a.effective_to)]
        lines = self.entries.get((employee_id, day), [])
        shared = profile.serves_all_projects
        if (not shared and len({a.project_id for a in covering}) <= 1
                and len({e.project_id for e in lines}) <= 1):
            return None
        owners, weights = {}, defaultdict(lambda: ZERO)
        for e in lines:
            if e.regular_hours > 0:
                key = (e.project_id, e.location_id, e.work_order_id)
                owners[key] = (e.project, e.location, e.work_order)
                weights[key] += e.regular_hours
        if not weights:                                              # no hours yet: an equal estimate
            owners = {(a.project_id, a.location_id, a.work_order_id): (a.project, a.location, a.work_order)
                      for a in covering}
            if shared:
                on = {a.project_id for a in covering}
                for p in self._active_projects(profile.employee.company_id):
                    if p.pk not in on:
                        owners[(p.pk, p.location_id, None)] = (p, p.location, None)
            weights = {key: ONE for key in owners}
        if not owners:
            return None
        total = sum(weights.values())
        return [(*owners[key], weight / total) for key, weight in weights.items()]
