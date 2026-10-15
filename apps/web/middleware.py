from urllib.parse import urlencode, urlparse

from django.conf import settings
from django.http import HttpResponse
from django.shortcuts import resolve_url


class HtmxSessionExpiredMiddleware:
    """When the session has ended, a background HTMX request (such as the Approvals counter) is redirected to the
    sign-in page. The browser follows that redirect itself, and HTMX would drop the whole sign-in page into the
    small box that asked. Answer with an HX-Redirect header instead, so the whole window goes to sign in."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if request.headers.get("HX-Request") and response.status_code in (301, 302):
            login_url = resolve_url(settings.LOGIN_URL)
            if response.get("Location", "").startswith(login_url):
                page = urlparse(request.headers.get("HX-Current-URL", ""))
                target = login_url
                if page.path and page.path != login_url:        # come back to the page they were on
                    back = page.path + (f"?{page.query}" if page.query else "")
                    target += "?" + urlencode({"next": back})
                redirect = HttpResponse(status=200)
                redirect["HX-Redirect"] = target
                return redirect
        return response