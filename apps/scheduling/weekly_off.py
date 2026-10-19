"""The company's weekly off, kept as dated rules. Imported at the bottom of models.py.

Until a rule is set, the built-in weekly off (Friday, from HRMS_DEFAULT_WEEKLY_OFF) applies. A new rule starts on a date
and closes the one before it, so earlier days keep the weekly off they had. A shift can still carry its own days; for
a person on a shift, the shift decides."""
from django.db import models
from django.db.models.signals import post_migrate
from django.dispatch import receiver

from apps.audit.registry import audited
from apps.core.models import BaseModel
from apps.core.scoping import CompanyQuerySet


@audited(module="scheduling", company="company_id")
class WeeklyOffRule(BaseModel):
    company = models.ForeignKey("organization.Company", on_delete=models.PROTECT, related_name="+")
    days = models.JSONField(help_text="Weekday numbers, Monday=0 to Sunday=6. For example [4] is Friday.")
    effective_from = models.DateField()
    effective_to = models.DateField(null=True, blank=True)

    objects = CompanyQuerySet.as_manager()

    class Meta:
        ordering = ["-effective_from"]
        constraints = [
            models.UniqueConstraint(fields=["company", "effective_from"], name="uniq_weekly_off_start"),
            models.UniqueConstraint(fields=["company"], condition=models.Q(effective_to__isnull=True),
                                    name="one_open_weekly_off_rule"),
            models.CheckConstraint(
                condition=models.Q(effective_to__isnull=True) | models.Q(effective_to__gte=models.F("effective_from")),
                name="weekly_off_ends_after_it_starts"),
        ]

    def __str__(self):
        return f"{self.company} {self.days} from {self.effective_from}"


# HR keeps the calendar; Management can see it. Given once per model, the first time its rights are missing from the
# group, so anything changed later on the Access tab survives future migrations.
RIGHTS = {
    "HR": {"holiday": ["view", "add", "change"], "weeklyoffrule": ["view", "add"]},
    "Management": {"holiday": ["view"], "weeklyoffrule": ["view"]},
}


@receiver(post_migrate)
def give_the_calendar_rights(sender, using="default", **kwargs):
    if sender.label != "scheduling":
        return
    from django.apps import apps as global_apps
    from django.contrib.auth.management import create_permissions
    from django.contrib.auth.models import Group, Permission

    create_permissions(sender, verbosity=0, using=using, apps=kwargs.get("apps", global_apps))
    for group_name, models_ in RIGHTS.items():
        group, _ = Group.objects.using(using).get_or_create(name=group_name)
        for model, actions in models_.items():
            perms = Permission.objects.using(using).filter(content_type__app_label="scheduling",
                                                           content_type__model=model)
            if not perms.exists() or group.permissions.filter(pk__in=perms).exists():
                continue
            group.permissions.add(*perms.filter(codename__in=[f"{a}_{model}" for a in actions]))
