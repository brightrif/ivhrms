from django.db import models

from apps.audit.registry import audited
from apps.core.models import BaseModel
from apps.core.scoping import CompanyQuerySet


@audited(module="configuration", company="company_id")
class SettingValue(BaseModel):
    """A chosen value. With no company it is the default for everyone; with a company it overrides that default for
    that company only. A setting nobody has chosen is simply absent and uses the built-in default."""
    key = models.CharField(max_length=100)
    company = models.ForeignKey("organization.Company", null=True, blank=True, on_delete=models.CASCADE,
                                related_name="+", help_text="Empty = the default for all companies")
    value = models.JSONField()

    objects = CompanyQuerySet.as_manager()

    class Meta:
        ordering = ["key", "company_id"]
        constraints = [
            models.UniqueConstraint(fields=["key", "company"], name="uniq_setting_key_company"),
            models.UniqueConstraint(fields=["key"], condition=models.Q(company__isnull=True),
                                    name="uniq_setting_key_default"),
        ]

    def __str__(self):
        return f"{self.key} = {self.value!r}" + (f" ({self.company.code})" if self.company_id else "")
