"""What the Settings page shows for Payroll, and the rights the Access grid offers for it.

Phase 1 has no plain switches: the allowance types are a list kept in their own table, so the tab shows them in a
custom section. Later phases add the dated payroll policy (days in a month, SIO rates) here."""
from apps.configuration.registry import Capability, Module, Section, register
from apps.organization.services import companies_for


def allowance_context(user):
    """One line per company the person can see: how many allowance types it has switched on."""
    from .models import AllowanceType

    rows = []
    for company in companies_for(user):
        types = AllowanceType.objects.filter(company=company)
        rows.append({"company": company, "active": types.filter(is_active=True).count(), "total": types.count()})
    return {"allowance_rows": rows}


register(Module(
    key="payroll", label="Payroll", order=55, icon="bi-cash-coin",
    sections=[
        Section(
            title="Allowance types",
            intro="The allowances each company pays on top of basic salary, such as housing or transport. "
                  "They are offered on every staff salary.",
            template="web/settings/_payroll_allowances.html", context=allowance_context),
    ],
    capabilities=[
        Capability("See staff salaries and payment details", "payroll.view_salaryrecord",
                   "Shows the Payroll page. Amounts and IBANs are visible only to people with this right."),
        Capability("Set salaries and payment details", "payroll.add_salaryrecord",
                   "A new salary starts on a date and the earlier one is kept in the history."),
        Capability("Correct the latest salary", "payroll.change_salaryrecord",
                   "Fixes a mistake in place. Use a new dated salary for a raise."),
        Capability("See allowance types", "payroll.view_allowancetype"),
        Capability("Add and change allowance types", "payroll.add_allowancetype",
                   "Also switches an allowance on or off."),
    ],
))
