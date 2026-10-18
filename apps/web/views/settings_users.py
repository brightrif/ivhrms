"""Settings > Users: create logins and give access (groups and companies). Superuser only, like the Access grid."""
from functools import wraps

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.cache import patch_vary_headers
from django.views.decorators.http import require_POST

from django.contrib.auth.validators import UnicodeUsernameValidator
from django.http import JsonResponse

from apps.accounts import user_admin
from apps.employees import services as employee_services
from apps.employees.models import Employee
from apps.organization.models import Company

from apps.web.forms.users import UserAccessForm, UserCreateForm
from apps.web.views.settings_hub import _visible

User = get_user_model()
PICK_LIMIT = 10


def superuser_only(view):
    @login_required
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_superuser:
            raise PermissionDenied
        return view(request, *args, **kwargs)
    return wrapper


def _page(request, template, context):
    context.update(tabs=_visible(request.user), show_access=True, tab="users")
    return render(request, template, context)


def _remember(request, user, credentials):
    """The temporary password is shown once, on the person's page, then forgotten."""
    request.session["new_user_login"] = {"user": user.pk, **credentials}


@superuser_only
def users_list(request):
    q, group, status = request.GET.get("q", "").strip(), request.GET.get("group", ""), request.GET.get("status", "")
    qs = User.objects.prefetch_related("groups", "company_access__company").order_by("username")
    if q:
        qs = qs.filter(Q(username__icontains=q) | Q(first_name__icontains=q) | Q(last_name__icontains=q)
                       | Q(email__icontains=q))
    if group.isdigit():
        qs = qs.filter(groups=int(group))
    if status == "active":
        qs = qs.filter(is_active=True)
    elif status == "off":
        qs = qs.filter(is_active=False)
    elif status == "no_group":
        qs = qs.filter(groups__isnull=True, is_superuser=False)
    elif status == "no_company":
        qs = qs.filter(company_access__isnull=True, is_superuser=False)
    page = Paginator(qs.distinct(), 25).get_page(request.GET.get("page"))
    for u in page:
        u.emp = getattr(u, "employee", None)
    params = request.GET.copy()
    params.pop("page", None)
    return _page(request, "web/settings/users.html", {
        "page": page, "extra": params.urlencode(), "q": q, "group": group, "status": status,
        "groups": Group.objects.order_by("name")})


@superuser_only
def users_add(request):
    form = UserCreateForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        cd = form.cleaned_data
        try:
            user, credentials = user_admin.create_user(
                request.user, username=cd["username"], first_name=cd["first_name"], last_name=cd["last_name"],
                email=cd["email"], groups=cd["groups"], companies=cd["companies"])
        except user_admin.UserAdminError as exc:
            form.add_error(None, str(exc))
        else:
            _remember(request, user, credentials)
            messages.success(request, f"The login {user.username} was created.")
            return redirect("web:settings_user", pk=user.pk)
    return _page(request, "web/settings/users_add.html", {"form": form})


@superuser_only
def users_for_employee(request):
    """Search first: employees who have no login yet, best matches only."""
    q = " ".join(request.GET.get("q", "").split())
    words = q.split()
    rows, hint, more = [], "", False
    if not any(len(w) >= 2 for w in words):
        hint = "Type at least 2 letters of the name, or part of the employee number."
    else:
        qs = (Employee.objects.for_user(request.user).filter(user__isnull=True)
              .exclude(status=Employee.Status.SEPARATED))
        for word in words:
            qs = qs.filter(Q(first_name__icontains=word) | Q(last_name__icontains=word)
                           | Q(employee_no__icontains=word))
        rows = list(qs.select_related("company", "designation").order_by("employee_no")[:PICK_LIMIT + 1])
        more, rows = len(rows) > PICK_LIMIT, rows[:PICK_LIMIT]
        for e in rows:
            e.suggested = employee_services.default_username(e)

        if not rows:
            hint = "Nobody without a login matches that."
    ctx = {"q": q, "rows": rows, "hint": hint, "more": more, "limit": PICK_LIMIT}
    partial = request.headers.get("HX-Request") == "true" and not request.headers.get("HX-History-Restore-Request")
    resp = _page(request, "web/settings/_users_for_employee_results.html" if partial
                 else "web/settings/users_for_employee.html", ctx)
    patch_vary_headers(resp, ["HX-Request"])
    return resp


@superuser_only
@require_POST
def users_create_login(request, pk):
    employee = get_object_or_404(Employee.objects.for_user(request.user), pk=pk)
    try:
        credentials = employee_services.create_login(employee,
                                                     username=request.POST.get("username", "").strip() or None)
    except employee_services.EmployeeError as exc:
        messages.error(request, str(exc))
        return redirect("web:settings_user_for_employee")
    employee.refresh_from_db()
    _remember(request, employee.user, credentials)
    messages.success(request, f"A login was created for {employee.full_name}. Now choose what they can reach.")
    return redirect("web:settings_user", pk=employee.user_id)


@superuser_only
def users_detail(request, pk):
    target = get_object_or_404(User, pk=pk)
    form = UserAccessForm(request.POST or None, target=target)
    if request.method == "POST" and form.is_valid():
        cd = form.cleaned_data
        try:
            changes = user_admin.set_access(request.user, target, groups=cd["groups"], companies=cd["companies"],
                                            is_active=cd["is_active"], username=cd["username"])
        except user_admin.UserAdminError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "Saved." if changes else "Nothing changed.")
            return redirect("web:settings_user", pk=target.pk)
    shown = request.session.get("new_user_login")
    credentials = None
    if shown and shown.get("user") == target.pk:
        credentials = request.session.pop("new_user_login")
    return _page(request, "web/settings/user_detail.html", {
        "target": target, "form": form, "credentials": credentials, "employee": getattr(target, "employee", None),
        "hints": user_admin.hints(target),
        "rights": [] if target.is_superuser else user_admin.effective_rights(target),
        "is_self": target.pk == request.user.pk, "companies_exist": Company.objects.filter(is_active=True).exists()})


@superuser_only
@require_POST
def users_reset(request, pk):
    target = get_object_or_404(User, pk=pk)
    credentials = user_admin.reset_password(request.user, target)
    _remember(request, target, credentials)
    messages.success(request, "A new temporary password was issued. It is shown once, below.")
    return redirect("web:settings_user", pk=target.pk)

@login_required
def username_check(request):
    """JSON for the live 'is this username free?' line. ?u=<name>&exclude=<user id to leave out>."""
    me = request.user
    if not (me.is_superuser or me.has_perm("employees.manage_logins") or me.has_perm("employees.add_employee")):
        raise PermissionDenied
    name = request.GET.get("u", "").strip()
    if not name:
        return JsonResponse({"state": "empty"})
    try:
        UnicodeUsernameValidator()(name)
        if len(name) > 150:
            raise ValidationError("too long")
    except ValidationError:
        return JsonResponse({"state": "invalid",
                             "message": "Letters, numbers and @ . + - _ only, with no spaces."})
    taken = User.objects.filter(username__iexact=name)
    if request.GET.get("exclude", "").isdigit():
        taken = taken.exclude(pk=int(request.GET["exclude"]))
    if taken.exists():
        return JsonResponse({"state": "taken", "message": f"'{name}' is already taken."})
    return JsonResponse({"state": "free", "message": "Available."})