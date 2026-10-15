"""Settings for the Docker test stack. Everything else comes from base.py and the environment (see docker-compose.yml).
The only additions: where collectstatic puts the files nginx serves, and the origins allowed to submit forms once the site
is served over HTTPS."""
from .base import *  # noqa: F401,F403

STATIC_ROOT = BASE_DIR / "staticfiles"                                    # noqa: F405
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])       # noqa: F405
