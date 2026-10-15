from apps.configuration.registry import Module, Section, register

from .services import companies_for


def company_rows(user):
    return {"project_rows": list(companies_for(user))}


def default_company_choices(user):
    return {"company_choices": list(companies_for(user))}


register(Module(
    key="organization", label="Organization", order=60, icon="bi-diagram-3",
    sections=[
        Section(
            title="Companies that run projects",
            intro="Switch on the companies that run projects. Only these are offered when you add a project. "
                  "A company switched off keeps the projects it already has.",
            template="web/settings/_organization_projects.html", context=company_rows),
        Section(
            title="Default company",
            intro="The company chosen first on pages that ask for one, such as Add staff member. "
                  "You can still pick another company on the page.",
            template="web/settings/_organization_default_company.html", context=default_company_choices),
    ]))