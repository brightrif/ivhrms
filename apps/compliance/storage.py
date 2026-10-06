"""Private file storage: scans of certificates and receipts are never served from a public URL.

Files live under PRIVATE_MEDIA_ROOT (outside the web server's static and media folders) and are
only reachable through permission-checked, audited download views.
"""
import os
import uuid
from pathlib import Path

from django.conf import settings
from django.core.files.storage import FileSystemStorage


class PrivateStorage(FileSystemStorage):
    @property
    def base_location(self):
        return settings.PRIVATE_MEDIA_ROOT       # read on every use, so test overrides work

    @property
    def location(self):
        return os.path.abspath(self.base_location)

    @property
    def base_url(self):
        return None                              # .url raises ValueError: there is no public address


def private_storage():
    return PrivateStorage()


def _extension(filename):
    return Path(filename).suffix.lower()[:10]


def document_path(instance, filename):
    return f"compliance/documents/{instance.company_id}/{uuid.uuid4().hex}{_extension(filename)}"


def receipt_path(instance, filename):
    return f"compliance/receipts/{instance.company_id}/{uuid.uuid4().hex}{_extension(filename)}"
