from apps.configuration.registry import Module, Section, register

from .models import Country
from .services import companies_for


def company_rows(user):
    return {"project_rows": list(companies_for(user))}


def default_company_choices(user):
    return {"company_choices": list(companies_for(user))}


def phone_countries(user):
    return {"countries": list(Country.objects.all())}


register(Module(
    key="organization", label="Organization", order=60, icon="bi-diagram-3",
    sections=[
        Section(
            title="Companies that run projects",
            intro="Switch on the companies that run projects. Only these are offered when you add a project. "
                  "A company switched off keeps the projects it already has.",
            template="web/settings/_organization_projects.html", context=company_rows),
        Section(
            title="Default companies",
            intro="The company each Add page starts on. Staff and labor can start on different companies. "
                  "You can still pick another company on the page.",
            template="web/settings/_organization_default_company.html", context=default_company_choices),
        Section(
            title="Countries",
            intro="Phone list: the countries offered in the country-code list of phone and WhatsApp boxes. "
                  "Nationality: the countries offered in the Nationality box on staff and labor forms. "
                  "Switch a country on to offer it, or add one that is not listed. Saved values are never changed.",
            template="web/settings/_organization_phone_codes.html", context=phone_countries),
    ]))