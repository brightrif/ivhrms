from apps.configuration.registry import Module, Section, register

from .services import companies_for


def company_rows(user):
    return {"project_rows": list(companies_for(user))}


register(Module(
    key="organization", label="Organization", order=10, icon="bi-diagram-3",
    sections=[
        Section(
            title="Companies that run projects",
            intro="Switch on the companies that run projects. Only these are offered when you add a project. "
                  "A company switched off keeps the projects it already has.",
            template="web/settings/_organization_projects.html", context=company_rows),
    ]))