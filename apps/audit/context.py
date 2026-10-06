from contextlib import contextmanager
from contextvars import ContextVar

_request = ContextVar("audit_request", default=None)
_overrides = ContextVar("audit_overrides", default={})


def set_request(request):
    return _request.set(request)


def reset_request(token):
    _request.reset(token)


def get_request():
    return _request.get()


def get_overrides():
    return _overrides.get()


@contextmanager
def audit_context(**overrides):
    """e.g. with audit_context(actor=user, channel="whatsapp"): ..."""
    token = _overrides.set({**_overrides.get(), **overrides})
    try:
        yield
    finally:
        _overrides.reset(token)

def current_user():
    actor = get_overrides().get("actor")
    if actor is not None:
        return actor
    request = get_request()
    user = getattr(request, "user", None) if request else None
    return user if user is not None and user.is_authenticated else None