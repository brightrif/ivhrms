import os
import uuid


def attachment_path(instance, filename):
    """Private, unguessable path: dependants/<company>/<dependant>/<random>.<ext>"""
    ext = os.path.splitext(filename)[1].lower()
    return f"dependants/{instance.company_id}/{instance.dependant_id}/{uuid.uuid4().hex}{ext}"
