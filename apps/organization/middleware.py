from . import scope


class CompanyScopeMiddleware:
    """Settles, once per request, which company a signed-in person is working in (see scope.py).
    Must come after the session and authentication middleware, and after the audit context middleware."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        scope.resolve(request)
        return self.get_response(request)
