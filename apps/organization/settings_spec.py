from apps.configuration.registry import Capability, Module, Section, register

from .models import Country
from .services import companies_for


def company_rows(user):
    return {"project_rows": list(companies_for(user))}


def default_company_choices(user):
    return {"company_choices": list(companies_for(user))}


def phone_countries(user):
    return {"countries": list(Country.objects.all())}


def company_view_context(user):
    from . import scope
    from .services import all_manageable_companies

    return {"company_view": scope.current_view(), "can_change": user.has_perm(scope.EDIT_PERM),
            "emblems": scope.EMBLEMS, "colour_rows": list(all_manageable_companies(user).order_by("name"))}


register(Module(
    key="organization", label="Organization", order=60, icon="bi-diagram-3",
    sections=[
        Section(
            title="Companies that run projects",
            intro="Switch on the companies that run projects. Only these are offered when you add a project. "
                  "A company switched off keeps the projects it already has.",
            template="web/settings/_organization_projects.html", context=company_rows),
        Section(
            title="Company view",
            intro="Whether people see all their companies together, or work in one company at a time. "
                  "Each company can have its own colours for the Separate view.",
            template="web/settings/_organization_company_view.html", context=company_view_context),
        Section(
            title="Countries",
            intro="Phone list: the countries offered in the country-code list of phone and WhatsApp boxes. "
                  "Nationality: the countries offered in the Nationality box on staff and labor forms. "
                  "Switch a country on to offer it, or add one that is not listed. Saved values are never changed.",
            template="web/settings/_organization_phone_codes.html", context=phone_countries),
    ],
    capabilities=[
        Capability("Change the company view and company colours", "configuration.edit_company_view",
                   "Chooses Combined or Separate for everyone. Only the superuser has it unless you give it here."),
    ]))
