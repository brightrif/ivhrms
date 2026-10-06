from django.test import TestCase
from apps.accounts.models import User
from .models import AuditEvent


class AuditTests(TestCase):
    def test_create_update_and_masking(self):
        u = User.objects.create_user("alice", password="secret")
        created = AuditEvent.objects.get(action="create", object_id=str(u.pk))
        self.assertEqual(created.changes["password"], {"masked": True})

        u.first_name = "Alice"
        u.save()
        updated = AuditEvent.objects.filter(action="update").first()
        self.assertEqual(updated.changes, {"first_name": {"old": "", "new": "Alice"}})

    def test_append_only(self):
        ev = AuditEvent.objects.create(action="view", module="test")
        with self.assertRaises(PermissionError):
            ev.save()
        with self.assertRaises(PermissionError):
            AuditEvent.objects.all().delete()