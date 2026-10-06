"""System data created automatically after every `migrate`. Nothing here needs a seed command.

Existing rows are never overwritten, so anything HR edits later (alert days, names, validity)
survives future migrations. Validity periods other than the CR are placeholders to confirm.
"""
from django.db import DEFAULT_DB_ALIAS, connections

DOCUMENT_TYPES = [
    dict(code="cr", name="Commercial Registration (CR)", name_ar="السجل التجاري",
         authority="Ministry of Industry and Commerce (Sijilat)", default_validity_months=12, is_mandatory=True),
    dict(code="municipality-licence", name="Municipality / Business Licence", name_ar="رخصة البلدية",
         authority="Municipality", default_validity_months=12),
    dict(code="chamber-membership", name="Chamber of Commerce Membership", name_ar="عضوية غرفة التجارة",
         authority="Bahrain Chamber of Commerce and Industry", default_validity_months=12),
    dict(code="civil-defence", name="Civil Defence Certificate", name_ar="شهادة الدفاع المدني",
         authority="Civil Defence", default_validity_months=12),
    dict(code="insurance-policy", name="Insurance Policy", name_ar="بوليصة التأمين",
         authority="Insurer", default_validity_months=12),
    dict(code="lease-contract", name="Premises Lease Contract", name_ar="عقد الإيجار",
         authority="Landlord", default_validity_months=12),
    dict(code="vehicle-registration", name="Vehicle Registration", name_ar="استمارة المركبة",
         authority="Traffic Directorate", default_validity_months=12),
    dict(code="bank-guarantee", name="Bank Guarantee", name_ar="الضمان البنكي",
         authority="Bank", default_validity_months=12),
]

_VIEW = ["view_document", "view_renewaltask", "view_renewalpayment"]

# Alert recipients are the document's responsible person plus users in these groups who can access the
# company. Management is added only when a document is within the escalation window or overdue.
GROUP_PERMISSIONS = {
    "HR": _VIEW + ["add_document", "change_document", "add_renewaltask", "change_renewaltask",
                   "add_renewalpayment", "view_documenttype", "add_documenttype", "change_documenttype"],
    "Finance": _VIEW + ["add_renewalpayment"],
    "Management": _VIEW,
}


def ensure_system_defaults(sender=None, using=DEFAULT_DB_ALIAS, **kwargs):
    # `migrate app_label` can emit post_migrate before this app's tables exist
    if "compliance_documenttype" not in connections[using].introspection.table_names():
        return
    from django.contrib.auth.models import Group, Permission

    from .models import DocumentType

    existing = set(DocumentType.objects.using(using).values_list("code", flat=True))
    missing = [DocumentType(**spec) for spec in DOCUMENT_TYPES if spec["code"] not in existing]
    if missing:
        # bulk_create sends no save signals, on purpose: this is system data, not something a person did,
        # so it must stay out of the audit trail (and out of every fresh database and test run).
        DocumentType.objects.using(using).bulk_create(missing)

    for group_name, codenames in GROUP_PERMISSIONS.items():
        group, _ = Group.objects.using(using).get_or_create(name=group_name)
        group.permissions.add(*Permission.objects.using(using).filter(
            content_type__app_label="compliance", codename__in=codenames))
