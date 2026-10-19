"""What the Settings page shows for Family Visas, and the rights the Access grid offers for it.

Every value is a starting point to confirm with the client and with the authority: nothing here is a legal ruling,
and each company can override the default for all companies."""
from decimal import Decimal

from apps.configuration.registry import Capability, Module, Section, Setting, register

def _approver_context(user):
    from .status_approver import approver_context
    return approver_context(user)


register(Module(
    key="dependants", label="Family Visas", order=45, icon="bi-people-fill",
    sections=[
        Section(
            title="Who the company puts forward",
            intro="Checked when someone asks for a new visa or a renewal. A cancellation is always allowed. "
                  "Confirm these with the client and the authority; they are only starting values.",
            settings=[
                Setting("dependants.allow_spouse", "Spouses", "bool", True, per_company=True),
                Setting("dependants.allow_children", "Children", "bool", True, per_company=True),
                Setting("dependants.allow_parents", "Parents", "bool", False, per_company=True,
                        help="Off by default: parents are usually a special case."),
                Setting("dependants.child_age_limit", "Children up to this age", "int", 18, per_company=True,
                        minimum=Decimal("0"), maximum=Decimal("30"),
                        help="A child older than this cannot be put forward. 0 means no limit."),
            ]),
        Section(
            title="Visa requests",
            settings=[
                Setting("dependants.require_papers", "Papers first", "bool", False, per_company=True,
                        help="When on, an approved request cannot be marked as submitted (or completed) until the "
                             "proof of relationship, passport and CPR are on file. Cancellations are never held up."),
            ]),
        Section(
            title="Who approves family status",
            intro="After HR sends a family status request on, the reporting manager decides it. A company with one "
                  "manager, or an owner who decides everything, can name that one person instead.",
            template="web/settings/_family_status_approver.html", context=_approver_context),
    ],
    capabilities=[
        Capability("See family members and their visa requests", "dependants.view_dependant"),
        Capability("Add and change family members", "dependants.add_dependant"),
        Capability("See attached papers (certificates, medical reports)", "dependants.view_dependantattachment",
                   "These can be sensitive. HR only by default."),
        Capability("Attach papers to a family member", "dependants.add_dependantattachment"),
        Capability("Raise family visa requests", "dependants.add_familyvisarequest"),
        Capability("Process visa requests: submit and record the result", "dependants.change_familyvisarequest"),
        Capability("See visa costs and receipts", "dependants.view_visapayment"),
        Capability("Record visa costs", "dependants.add_visapayment"),
        Capability("Decide who may have a family visa", "dependants.change_familyvisaexception",
                   "Allow or refuse the family visa option for one person. HR and Management by default."),
    ],
))
