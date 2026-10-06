from django.contrib.auth.models import AbstractUser
from django.db import models
from apps.audit.registry import audited


@audited(exclude=["last_login"], mask=["password"])
class User(AbstractUser):
    """Custom user. Will be linked to Employee later."""
    must_change_password = models.BooleanField(default=False)