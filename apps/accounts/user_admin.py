"""Creating logins and giving access: which groups a person is in and which companies they work in.

Used by Settings > Users (superuser only, like the Access grid). It changes only group membership, company access and
whether the login is switched on. What each group may do is set on the Access tab."""
from django.contrib.auth import get_user_model
from django.db import IntegrityError,transaction
from django.utils.crypto import get_random_string

from apps.audit import services as audit
from apps.configuration import access
from apps.organization.models import CompanyAccess

_PASSWORD_CHARS = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"      # no look-alike characters


class UserAdminError(Exception):
    pass


def _temporary_password():
    return get_random_string(12, _PASSWORD_CHARS)


def _log(actor, user, text):
    audit.log("update", user, module="configuration", subject_employee_id=None, company_id=None,
              reason=f"{actor.get_username()} {text}")


@transaction.atomic
def create_user(actor, *, username, first_name="", last_name="", email="", groups=(), companies=()):
    """A login with a temporary password (shown once; the person must change it at first sign-in)."""
    User = get_user_model()
    username = (username or "").strip()
    if not username:
        raise UserAdminError("Give a username.")
    if User.objects.filter(username__iexact=username).exists():
        raise UserAdminError(f"The username '{username}' is already taken.")
    password = _temporary_password()
    user = User(username=username, first_name=(first_name or "").strip(), last_name=(last_name or "").strip(),
                email=(email or "").strip(), must_change_password=True)

    user.set_password(password)
    try:
        with transaction.atomic():
            user.save()
    except IntegrityError:                    # two people saved the same name at the same moment
        raise UserAdminError(f"The username '{username}' is already taken.")
    _log(actor, user, f"created the login {username}")

    set_access(actor, user, groups=groups, companies=companies)
    return user, {"username": user.username, "password": password}


@transaction.atomic
def set_access(actor, user, *, groups, companies, is_active=None, username=None):
    """Make the person's groups and companies match the lists given. Returns what changed, in words."""
    changes = []
    if username is not None and username != user.username:
        old = user.username
        user.username = username
        try:
            with transaction.atomic():
                user.save(update_fields=["username"])
        except IntegrityError:
            raise UserAdminError(f"The username '{username}' is already taken.")
        changes.append(f"changed the username {old} to {username}")
        
    wanted = {g.pk: g for g in groups}
    have = {g.pk: g for g in user.groups.all()}
    for pk, group in wanted.items():
        if pk not in have:
            user.groups.add(group)
            changes.append(f"put {user.get_username()} in the group '{group.name}'")
    for pk, group in have.items():
        if pk not in wanted:
            user.groups.remove(group)
            changes.append(f"took {user.get_username()} out of the group '{group.name}'")

    wanted_c = {c.pk: c for c in companies}
    have_c = {a.company_id: a for a in CompanyAccess.objects.filter(user=user).select_related("company")}
    for pk, company in wanted_c.items():
        if pk not in have_c:
            CompanyAccess.objects.create(user=user, company=company)
            changes.append(f"gave {user.get_username()} access to {company.name}")
    for pk, row in have_c.items():
        if pk not in wanted_c:
            row.delete()
            changes.append(f"took away {user.get_username()}'s access to {row.company.name}")

    if is_active is not None and is_active != user.is_active:
        if not is_active:
            if user.pk == actor.pk:
                raise UserAdminError("You cannot switch off your own login.")
            others = get_user_model().objects.filter(is_superuser=True, is_active=True).exclude(pk=user.pk)
            if user.is_superuser and not others.exists():
                raise UserAdminError("This is the only active superuser. Make another one first.")
        user.is_active = is_active
        user.save(update_fields=["is_active"])
        changes.append(f"switched {user.get_username()}'s login {'on' if is_active else 'off'}")
    for text in changes:
        _log(actor, user, text)
    return changes


@transaction.atomic
def reset_password(actor, user):
    """A new temporary password (shown once). The person must choose their own at the next sign-in."""
    password = _temporary_password()
    user.set_password(password)
    user.must_change_password = True
    user.save()
    _log(actor, user, f"issued a new temporary password for {user.get_username()}")
    return {"username": user.username, "password": password}


def effective_rights(user):
    """The rights from the Access grid that this person has now, module by module: [(module label, [labels])]."""
    found = {}
    for row in access.rows():
        if user.has_perm(row.perm):
            found.setdefault(row.module.label, []).append(row.label)
    return list(found.items())


def hints(user):
    """Plain-words reasons why a person may see little or nothing, as [(level, text)]; level is warning or info."""
    out = []
    if not user.is_active:
        out.append(("warning", "The login is switched off, so this person cannot sign in."))
    if user.is_superuser:
        out.append(("info", "A superuser can do everything, in every company."))
    else:
        if not user.groups.exists():
            out.append(("warning", "In no group, so only their own pages work (dashboard, leave, attendance). "
                                   "Put them in a group to give them the HR, Finance or Management pages."))
        if not CompanyAccess.objects.filter(user=user).exists():
            out.append(("warning", "No company access, so lists of people and records will be empty. "
                                   "Tick the companies they work in."))
    if getattr(user, "employee", None) is None:
        out.append(("info", "Not linked to an employee record, so the My pages (leave, family, air tickets) are not "
                            "available to this login."))
    if user.must_change_password:
        out.append(("info", "Has not chosen their own password yet."))
    return out
