"""Who may open a page: permission checks and the employee-record check shared by every view module."""

from functools import wraps

from django.contrib.auth.decorators import login_required, permission_required
from django.shortcuts import render


def employee_required(view):
    @login_required
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        employee = getattr(request.user, "employee", None)
        if employee is None:
            return render(request, "web/no_employee.html", status=403)
        request.employee = employee
        return view(request, *args, **kwargs)
    return wrapper


def hr_perm(perm):
    def decorator(view):
        return login_required(permission_required(perm, raise_exception=True)(view))
    return decorator
