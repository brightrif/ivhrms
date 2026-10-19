"""Designations and Grades that can belong to one company, or be shared by all of them.

A row with no company is SHARED: every company offers it (this is how they all started). A row with a company belongs
to that company only. The Company view (see scope.py) decides what a screen shows:

  Combined   every row, as before
  Separate   the shared rows plus the rows of the company the login is working in

A row created while someone works in a company in the Separate view belongs to that company. Created in the Combined
view (or from the shell) it is shared. `split_designations_grades` turns the shared rows into one copy per company."""
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q

from .scope import current_scope

IDENTITY = {"Designation": "name", "Grade": "code"}      # what makes two rows "the same one"
_SKIP = {"id", "company", "created_at", "updated_at", "created_by", "updated_by"}


class CompanyListQuerySet(models.QuerySet):
    def for_company(self, company):
        """The rows one company offers: its own plus the shared ones."""
        return self.filter(Q(company__isnull=True) | Q(company_id=getattr(company, "pk", company)))


class CompanyListManager(models.Manager.from_queryset(CompanyListQuerySet)):
    """Narrows itself to the company being worked in (Separate view). Related objects (employee.designation) are
    reached through the plain base manager, so a record never loses a designation it already has."""

    def get_queryset(self):
        queryset = super().get_queryset()
        scope = current_scope()
        if scope is not None:
            queryset = queryset.filter(Q(company__isnull=True) | Q(company_id=scope))
        return queryset


def claim_current_company(instance):
    """A new row made in the Separate view belongs to the company being worked in."""
    if instance._state.adding and instance.company_id is None:
        scope = current_scope()
        if scope is not None:
            instance.company_id = scope


def check_unique(instance, field, label):
    """The value must not clash with a row this company would also see (its own, a shared one, or any if shared)."""
    value = getattr(instance, field)
    if not value:
        return
    target = instance.company_id
    if target is None and instance._state.adding:
        target = current_scope()
    same = type(instance)._base_manager.filter(**{f"{field}__iexact": value}).exclude(pk=instance.pk)
    if target is not None:
        same = same.filter(Q(company__isnull=True) | Q(company_id=target))
    if same.exists():
        raise ValidationError({field: f"That {label} is already used."})


# ---------------------------------------------------------------- one copy per company

def _copy_fields(row):
    return {f.name: getattr(row, f.name) for f in row._meta.concrete_fields if f.name not in _SKIP}


def _has_company(model):
    return any(f.name == "company" for f in model._meta.concrete_fields)


def split_shared(apply=False):
    """Give every company its own copy of each shared designation and grade, move each company's records onto its own
    copy, and delete the shared row once nothing uses it. Without apply=True nothing is written.

    Returns one dict per shared row: kind, name, new (copies made), moved (records moved), deleted, left (why the shared
    row stayed). Running it again finds no shared rows, so it does nothing."""
    from apps.audit import services as audit

    from .models import Company, Designation, Grade

    companies = list(Company.objects.order_by("name"))
    report = []
    with transaction.atomic():
        for model in (Designation, Grade):
            relations = [r for r in model._meta.get_fields(include_hidden=True)
                         if r.auto_created and not r.concrete and (r.one_to_many or r.one_to_one)]
            key = IDENTITY[model.__name__]
            for shared in list(model._base_manager.filter(company__isnull=True).order_by("pk")):
                row = {"kind": model.__name__, "name": str(shared), "new": [], "moved": 0, "deleted": False, "left": ""}
                total = sum(r.related_model._base_manager.filter(**{r.field.name: shared}).count() for r in relations)
                for company in companies:
                    copy = model._base_manager.filter(company=company, **{key: getattr(shared, key)}).first()
                    if copy is None:
                        row["new"].append(company.name)
                        if apply:
                            copy = model(company=company, **_copy_fields(shared))
                            copy.save()
                    moved_here = 0
                    for rel in relations:
                        if not _has_company(rel.related_model):
                            continue
                        rows = rel.related_model._base_manager.filter(**{rel.field.name: shared, "company_id": company.pk})
                        moved_here += rows.count()
                        if apply and copy is not None:
                            rows.update(**{rel.field.name: copy})
                    row["moved"] += moved_here
                    if apply and moved_here:
                        audit.log("update", shared, module="organization", company_id=company.pk,
                                  reason=f"Split per company: {moved_here} record(s) moved to {company.name}'s own copy")
                if total - row["moved"] > 0:
                    row["left"] = f"{total - row['moved']} record(s) not tied to a company still use it"
                else:
                    row["deleted"] = True
                    if apply:
                        shared.delete()
                report.append(row)
    return report
