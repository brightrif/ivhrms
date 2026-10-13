"""What the Settings page shows for Air Tickets, and the rights the Access grid offers for it."""
from decimal import Decimal

from apps.configuration.registry import Capability, Module, Section, Setting, register

register(Module(
    key="airtickets", label="Air Tickets", order=46, icon="bi-airplane",
    sections=[
        Section(
            title="Reminders",
            intro="Every morning the system looks at each employee's next air ticket. HR users of the company get one "
                  "summary email listing everyone whose ticket is coming up, once when it enters the window and once "
                  "more when it falls due. Nothing is sent twice for the same date.",
            settings=[
                Setting("airtickets.reminder_days", "Remind this many days before a ticket falls due", "int", 60,
                        per_company=True, minimum=Decimal("0"), maximum=Decimal("365"),
                        help="0 turns the reminders off."),
                Setting("airtickets.remind_employee", "Also email the employee", "bool", True, per_company=True,
                        help="Only if the employee has an email address. It tells them they can request the ticket."),
            ]),
    ],
    capabilities=[
        Capability("See air tickets, policies, requests and reports", "airtickets.view_ticketentitlement"),
        Capability("Add and change ticket policies", "airtickets.change_ticketpolicy"),
        Capability("Record tickets already taken and refresh eligibility", "airtickets.add_ticketentitlement"),
        Capability("Edit travel details", "airtickets.change_travelprofile"),
        Capability("Ask for an air ticket on someone's behalf", "airtickets.add_ticketrequest"),
        Capability("Issue tickets, record allowances, cancel requests", "airtickets.change_ticketrequest"),
    ],
))
