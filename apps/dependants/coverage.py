from collections import defaultdict

from apps.compliance.models import Document, DocumentType


def missing_by_dependant(dependants):
    """{dependant pk: [mandatory DocumentType with no current document]} for a whole page, in two queries."""
    ids = [d.pk for d in dependants]
    if not ids:
        return {}
    types = list(DocumentType.objects.filter(applies_to=DocumentType.AppliesTo.DEPENDANT, is_active=True,
                                             is_mandatory=True).order_by("name"))
    held = defaultdict(set)
    rows = Document.objects.filter(is_current=True, dependant_id__in=ids).values_list("dependant_id", "document_type_id")
    for dependant_id, type_id in rows:
        held[dependant_id].add(type_id)
    return {i: [t for t in types if t.pk not in held[i]] for i in ids}
