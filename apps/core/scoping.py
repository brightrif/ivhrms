from django.db import models


class CompanyQuerySet(models.QuerySet):
    def for_user(self, user, field="company"):
        """Limit to companies the user may access. `field` can be a path, e.g. 'employee__company'."""
        from apps.organization.services import companies_for   # lazy import avoids an import cycle
        return self.filter(**{f"{field}__in": companies_for(user)})