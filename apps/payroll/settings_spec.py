"""What the Settings page shows for Payroll, and the rights the Access grid offers for it.

Phase 1 has no plain switches: the allowance types are a list kept in their own table, so the tab shows them in a
custom section. Later phases add the dated payroll policy (days in a month, SIO rates) here."""
from apps.configuration.registry import Capability, Module, Section, register
from apps.organization.services import companies_for


def policy_context(user):
    """One line per company the person can see: the payroll policy in force today."""
    from datetime import date

    from .policy import policy_on

    return {"policy_rows": [{"company": c, "policy": policy_on(c, date.today())} for c in companies_for(user)]}


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
            title="Payroll policy",
            intro="Days in a month and the SIO rules, for each company. They are dated, so a change starts on a date "
                  "you choose and earlier months stay as they were. SIO rates can stay empty until the accountant "
                  "gives them; payroll then stops at any Bahraini rather than guess.",
            template="web/settings/_payroll_policy.html", context=policy_context),
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
        Capability("See payroll runs and their payment lists", "payroll.view_payrollrun",
                   "Shows every figure in a run: gross, deductions, SIO and net pay."),
        Capability("Prepare payroll runs", "payroll.add_payrollrun",
                   "Start a run, recalculate it, add or remove adjustments, cancel a draft."),
        Capability("Send a payroll run for approval", "payroll.submit_payrollrun",
                   "Management then decides it in the Approvals inbox."),
        Capability("Record payroll payments", "payroll.pay_payrollrun", "Marks people as paid, by bank or cash."),
        Capability("See final settlements", "payroll.view_finalsettlement",
                   "Shows what a leaver is owed. Give it together with the right to see payroll runs, which shows the Payroll menu."),
        Capability("Prepare final settlements", "payroll.add_finalsettlement",
                   "Start a settlement, recalculate it, add lines, pick up charges, cancel a draft."),
        Capability("Send a final settlement for approval", "payroll.submit_finalsettlement",
                   "Management then decides it in the Approvals inbox. Approval marks the person as Separated."),
        Capability("Record final settlement payments", "payroll.pay_finalsettlement"),
        Capability("See the payroll policy", "payroll.view_payrollpolicy"),
        Capability("Set the payroll policy", "payroll.add_payrollpolicy", "Days in a month and the SIO rules; dated."),
        Capability("See allowance types", "payroll.view_allowancetype"),
        Capability("Add and change allowance types", "payroll.add_allowancetype",
                   "Also switches an allowance on or off."),
    ],
))
