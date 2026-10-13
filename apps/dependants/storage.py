import os
import uuid


def attachment_path(instance, filename):
    """Private, unguessable path: dependants/<company>/<dependant>/<random>.<ext>"""
    ext = os.path.splitext(filename)[1].lower()
    return f"dependants/{instance.company_id}/{instance.dependant_id}/{uuid.uuid4().hex}{ext}"


def receipt_path(instance, filename):
    """Private, unguessable path for a visa payment receipt."""
    ext = os.path.splitext(filename)[1].lower()[:10]
    return f"dependants/receipts/{instance.company_id}/{uuid.uuid4().hex}{ext}"
