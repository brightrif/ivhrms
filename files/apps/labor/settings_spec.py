"""What the Settings page shows for Labor, and the rights the Access grid offers for it.

The overtime rules are dated and affect pay, so they are not simple switches: they keep their own table and the tab
shows them in a custom section. Everything else here only changes what a screen starts with."""
from datetime import date
from decimal import Decimal

from apps.configuration.registry import Capability, Module, Section, Setting, register
from apps.organization.services import companies_for


def overtime_context(user):
    """One line per company the person can see: what it has decided about overtime, and the rules in force."""
    from .overtime_services import overtime_mode

    today = date.today()
    rows = []
    for company in companies_for(user):
        mode, policy = overtime_mode(company, today)
        rows.append({"company": company, "mode": mode, "policy": policy})
    return {"overtime_rows": rows}


register(Module(
    key="labor", label="Labor", order=50, icon="bi-people",
    sections=[
        Section(
            title="Overtime",
            intro="Whether each company pays overtime, and at what rates. These are dated and affect pay, so a change "
                  "starts on a date you choose and earlier months stay as they were.",
            template="web/settings/_labor_overtime.html", context=overtime_context),
        Section(title="Workers", settings=[
            Setting("labor.default_standard_hours", "Standard hours per day for new workers", "decimal", Decimal("8"),
                    help="What the form starts with when you add a worker. Hours beyond this count as overtime.",
                    minimum=Decimal("1"), maximum=Decimal("24")),
            Setting("labor.new_worker_overtime_eligible", "New workers eligible for overtime by default", "bool", True,
                    help="Only counts if the company pays overtime. It can be changed for each worker."),
        ]),
        Section(title="Timesheets", settings=[
            Setting("labor.timesheet_fill_standard", "Start with \"fill the day's standard hours\" chosen", "bool", False,
                    help="Saves a click when most days are standard. Nothing is filled until you press Save."),
        ]),
        Section(title="Sheets", settings=[
            Setting("labor.sheet_period", "Period the attendance and timesheet sheets open on", "choice", "week",
                    choices=(("day", "Day"), ("week", "Week (Sunday to Saturday)"), ("month", "Month")),
                    help="The overtime tab always opens on the month."),
            Setting("labor.attendance_fill_present", "Start with \"mark empty working days Present\" chosen", "bool", False,
                    help="Nothing is filled until you press Save."),
        ]),
    ],
    capabilities=[
        Capability("Add labor workers and change their details", "labor.add_laborprofile"),
        Capability("See pay rates, the cost reports and contractor statements", "labor.view_laborrate",
                   "Money columns are shown only to people with this right."),
        Capability("Enter and change pay rates", "labor.add_laborrate", "Pay terms are replaced by a new dated row, never edited."),
        Capability("See sites, the deployment and manpower reports", "labor.view_laborallocation"),
        Capability("Allocate and move workers between sites", "labor.add_laborallocation"),
        Capability("Record site attendance and hours", "labor.add_timeentry"),
        Capability("Confirm timesheets", "labor.change_timeentry", "Locks the hours for a site and period."),
        Capability("Reopen confirmed timesheets", "labor.delete_timeentry", "Unlocks hours so they can be corrected."),
        Capability("See overtime rules and claims", "labor.view_overtimeclaim"),
        Capability("Set the overtime decision and rates", "labor.add_overtimepolicy", "Dated; earlier claims keep their rules."),
        Capability("Prepare overtime claims", "labor.add_overtimeclaim"),
        Capability("Approve or reject overtime claims", "labor.change_overtimeclaim", "Decisions are final."),
        Capability("Void overtime claims", "labor.delete_overtimeclaim", "Needed to reopen a timesheet whose overtime was decided."),
    ],
))
