"""Company view: do people see all their companies together, or work in one company at a time?

Two modes, chosen under Settings > Organization by the superuser (or anyone the superuser gives the right to):

  Combined  everything works as before: a screen shows every company the person has access to.
  Separate  a login works in one company at a time. A person with one company sees only that company. A person with
            two gets a switch in the top bar and starts on the company that was set up first.

The mode is one value kept in SystemSetting. The middleware reads it once per request and leaves the answer on the
request, so no screen pays for it. services.companies_for() looks at that answer, which narrows every screen that
already asks it which companies to show. Outside a web request (shell, tests, background tasks) nothing is narrowed.
"""
import re

from django.db import transaction

COMBINED = "combined"
SEPARATE = "separate"
VIEWS = ((COMBINED, "Combined"), (SEPARATE, "Separate"))

SETTING_KEY = "company_view"
SESSION_KEY = "work_company_id"
EDIT_PERM = "configuration.edit_company_view"

_COLOUR = re.compile(r"^#[0-9a-fA-F]{6}$")


# ---------------------------------------------------------------- the setting

def current_view():
    """COMBINED or SEPARATE. Anything missing or unreadable counts as Combined."""
    from apps.core.models import SystemSetting

    value = SystemSetting.objects.filter(key=SETTING_KEY).values_list("value", flat=True).first()
    return value if value in (COMBINED, SEPARATE) else COMBINED


@transaction.atomic
def set_view(mode):
    """Choose the company view for everyone. The change is written to the audit log."""
    from apps.audit import services as audit
    from apps.core.models import SystemSetting

    if mode not in (COMBINED, SEPARATE):
        raise ValueError(f"Unknown company view {mode!r}")
    before = current_view()
    row, _ = SystemSetting.objects.get_or_create(
        key=SETTING_KEY, defaults={"value": mode, "description": "Company view: combined or separate"})
    if row.value != mode:
        row.value = mode
        row.save()
    if before != mode:
        audit.log("update", row, module="organization", changes={"company_view": {"old": before, "new": mode}})
    return mode


# ---------------------------------------------------------------- who is working in which company

def resolve(request):
    """Work out, once per request, which company this login is working in. Leaves four things on the request:

    company_view_separate  True when the view is Separate and the person is signed in
    work_companies         the companies the person may switch between, default company first
    work_company           the one they are working in now (None in Combined view)
    company_scope          that company's id, or None when nothing is narrowed
    """
    request.company_view_separate = False
    request.work_company = None
    request.work_companies = []
    request.company_scope = None

    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return
    if current_view() != SEPARATE:
        return

    from .services import all_companies_for        # lazy: services imports this module too

    companies = sorted(all_companies_for(user), key=lambda c: c.pk)
    request.company_view_separate = True
    request.work_companies = companies
    if not companies:
        return

    session = getattr(request, "session", None)
    stored = session.get(SESSION_KEY) if session is not None else None
    chosen = next((c for c in companies if c.pk == stored), companies[0])
    if session is not None and stored != chosen.pk:
        session[SESSION_KEY] = chosen.pk
    request.work_company = chosen
    request.company_scope = chosen.pk


def switch(request, company_id):
    """Move this login to another company. Returns the company, or None if the person has no access to it."""
    from .services import all_companies_for

    company = all_companies_for(request.user).filter(pk=company_id).first()
    if company is not None:
        request.session[SESSION_KEY] = company.pk
    return company


def active_scope_for(user):
    """The one company the current request is limited to, or None when the whole access list applies.
    Only narrows for the person who is signed in on this request, never for someone else's list."""
    from apps.audit.context import get_request

    request = get_request()
    scope = getattr(request, "company_scope", None) if request is not None else None
    if scope is None:
        return None
    if getattr(getattr(request, "user", None), "pk", None) != getattr(user, "pk", None):
        return None
    return scope


# ---------------------------------------------------------------- colours

def clean_colour(raw):
    """'#1A5FA8' -> '#1a5fa8'; empty -> ''. Anything else raises ValueError with words for the person."""
    raw = (raw or "").strip()
    if not raw:
        return ""
    if not _COLOUR.match(raw):
        raise ValueError("Enter a colour like #1a5fa8.")
    return raw.lower()


def _rgb(colour):
    return tuple(int(colour[i:i + 2], 16) for i in (1, 3, 5))


def _shade(colour, factor):
    r, g, b = (max(0, min(255, round(c * factor))) for c in _rgb(colour))
    return f"#{r:02x}{g:02x}{b:02x}"


def _tint(colour, amount):
    """Mix a colour with white: 0 is the colour itself, 1 is white."""
    r, g, b = (round(c + (255 - c) * amount) for c in _rgb(colour))
    return f"#{r:02x}{g:02x}{b:02x}"


def _rgb_text(colour):
    return ", ".join(str(c) for c in _rgb(colour))


def build_theme(company):
    """What the page needs to paint a company's colours, or None when it has chosen none (standard colours). The names
    follow the variables in app.css: --iv-blue and friends, the Bootstrap primary tints and --sidebar-bg."""
    primary, sidebar = company.theme_color, company.sidebar_color
    if not (primary or sidebar):
        return None
    theme = {}
    if primary:
        r, g, b = _rgb(primary)
        hover, accent = _shade(primary, 0.85), _tint(primary, 0.5)
        theme.update(primary=primary, primary_rgb=_rgb_text(primary), hover=hover, hover_rgb=_rgb_text(hover),
                     active=_shade(primary, 0.75), accent=accent, accent_rgb=_rgb_text(accent), focus=_tint(primary, 0.5),
                     subtle=_tint(primary, 0.88), subtle_border=_tint(primary, 0.7), emphasis=_shade(primary, 0.6),
                     on_primary="#000" if (r * 299 + g * 587 + b * 114) / 1000 > 160 else "#fff")
    if sidebar:
        theme["sidebar"] = sidebar
    return theme


def current_scope():
    """The company id the current web request is limited to (Separate view), or None. Unlike active_scope_for() it does
    not ask who is asking: lists such as designations and grades follow whoever is signed in on the request."""
    from apps.audit.context import get_request

    request = get_request()
    return getattr(request, "company_scope", None) if request is not None else None
