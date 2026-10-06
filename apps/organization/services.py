import re

from django.db import models, transaction
from django.db.models import ProtectedError, RestrictedError

from .models import Company, CompanyAccess, Department


class CompanyError(Exception):
    pass


def companies_for(user):
    """Active companies the user may work in. Scopes every working screen."""
    if user.is_superuser:
        return Company.objects.filter(is_active=True)
    return Company.objects.filter(access__user=user, is_active=True)


def manageable_companies(user):
    """Every company the user may manage, inactive ones included."""
    if user.is_superuser:
        return Company.objects.all()
    return Company.objects.filter(access__user=user)


def active_employee_count(company):
    from apps.employees.models import Employee     # lazy: employees depends on organization, not the reverse
    return company.employees.exclude(status=Employee.Status.SEPARATED).count()


def blockers(obj):
    """Records that stop `obj` being deleted: {label: count}. Setup rows (CASCADE) are not listed."""
    found = {}
    for rel in obj._meta.get_fields(include_hidden=True):
        if not (rel.auto_created and not rel.concrete and (rel.one_to_one or rel.one_to_many)):
            continue
        if rel.on_delete is models.CASCADE:
            continue
        model = rel.related_model
        count = model._base_manager.filter(**{rel.field.name: obj.pk}).count()
        if count:
            label = str(model._meta.verbose_name_plural)
            if model is type(obj):
                label = f"sub-{label}"
            found[label] = found.get(label, 0) + count
    return found


@transaction.atomic
def create_company(user, company):
    """Save a new (unsaved) Company. A non-superuser creator is given access to it."""
    company.save()
    if not user.is_superuser:
        CompanyAccess.objects.get_or_create(user=user, company=company)
    return company


@transaction.atomic
def set_active(company, active):
    if not active:
        n = active_employee_count(company)
        if n:
            raise CompanyError(f"{company.name} still has {n} employee(s) who have not left the company. "
                               "Separate or move them first.")
    company.is_active = active
    company.save(update_fields=["is_active"])


@transaction.atomic
def delete_company(company):
    blocking = blockers(company)
    if blocking:
        raise CompanyError(f"{company.name} still has records that must be kept. Deactivate it instead.")
    try:
        company.delete()
    except (ProtectedError, RestrictedError) as exc:
        raise CompanyError("Something linked to this company still depends on it. Deactivate it instead.") from exc


# ---------------------------------------------------------------- departments

def suggest_department_code(company, name):
    """'Human Resources' -> HR, 'Operations' -> OPE; a number is added if the code is taken."""
    words = re.findall(r"[A-Za-z0-9]+", name)
    if len(words) > 1:
        base = "".join(w[0] for w in words[:4]).upper()
    elif words:
        base = words[0][:3].upper()
    else:
        base = "DEP"
    code, n = base, 1
    while Department.objects.filter(company=company, code=code).exists():
        n += 1
        code = f"{base}{n}"
    return code


def descendant_ids(department):
    """Every department below this one, so a department can never be placed under its own child."""
    found, frontier = set(), [department.pk]
    while frontier:
        children = [c for c in Department.objects.filter(parent_id__in=frontier).values_list("pk", flat=True)
                    if c not in found]
        found.update(children)
        frontier = children
    return found