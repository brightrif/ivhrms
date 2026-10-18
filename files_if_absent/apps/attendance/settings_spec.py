"""What the Settings page shows for Attendance, and the rights the Access grid offers for it."""
from apps.configuration.registry import Capability, Module, Section, Setting, register

register(Module(
    key="attendance", label="Attendance", order=43, icon="bi-calendar-check",
    sections=[
        Section(title="Staff attendance sheet", settings=[
            Setting("attendance.staff_sheet_period", "Period the staff attendance sheet opens on", "choice", "week",
                    choices=(("day", "Day"), ("week", "Week (Sunday to Saturday)"), ("month", "Month")),
                    per_company=True,
                    help="The sheet can always be switched to another period with the Day, Week and Month buttons."),
        ]),
    ],
    capabilities=[
        Capability("Give attendance for staff", "attendance.add_attendance",
                   "The Staff attendance page. A day that is already recorded never changes there, except a weekly "
                   "off or holiday the system recorded, which HR can replace when someone worked."),
    ],
))
