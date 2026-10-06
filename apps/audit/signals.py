from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.dispatch import receiver

from .services import log


@receiver(user_logged_in)
def on_login(sender, request, user, **kwargs):
    log("login", user, module="accounts", actor=user)


@receiver(user_logged_out)
def on_logout(sender, request, user, **kwargs):
    if user is not None:
        log("logout", user, module="accounts", actor=user)


@receiver(user_login_failed)
def on_login_failed(sender, credentials, request=None, **kwargs):
    attempted = credentials.get("username") or credentials.get("email") or ""
    log("login_failed", module="accounts", object_repr=str(attempted))