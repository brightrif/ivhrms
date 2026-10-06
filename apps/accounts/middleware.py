from django.http import HttpResponse
from django.shortcuts import redirect
from django.urls import reverse


class ForcePasswordChangeMiddleware:
    """Users on a temporary password can only reach the change-password page (and log out)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated and user.must_change_password:
            target = reverse("web:password_change")
            if request.path not in (target, reverse("web:logout")):
                if request.headers.get("HX-Request"):
                    resp = HttpResponse(status=204)
                    resp["HX-Redirect"] = target
                    return resp
                return redirect(target)
        return self.get_response(request)