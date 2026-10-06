from datetime import date, time

from django.contrib.auth.models import Permission
from django.core.management import call_command
from django.urls import reverse

from apps.accounts.models import User
from apps.attendance.tests import BaseCase
from apps.audit.models import AuditEvent
from apps.core.models import ApprovalFlow
from apps.leave.models import LeaveType
from apps.organization.models import Company, CompanyAccess, Department
from apps.scheduling.models import Holiday, Shift


class CompanyPageTests(BaseCase):
    def setUp(self):
        super().setUp()
        self.root = User.objects.create_superuser("root", password="x")
        self.client.force_login(self.root)

    def post_new(self, code="iv2", name="IV Two"):
        return self.client.post(reverse("web:company_create"),
                                {"code": code, "name": name, "currency": "bhd"})

    def test_pages_require_permission(self):
        self.client.force_login(self.hr_user)          # the HR group has staff permissions only
        for name in ("web:company_list", "web:company_create"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 403)

    def test_create_then_edit_keeps_the_code(self):
        self.assertRedirects(self.post_new(), reverse("web:company_list"))
        c = Company.objects.get(name="IV Two")
        self.assertEqual((c.code, c.currency), ("IV2", "BHD"))
        self.assertTrue(AuditEvent.objects.filter(module="organization", action="create",
                                                  object_id=str(c.pk)).exists())
        self.client.post(reverse("web:company_edit", args=[c.pk]),
                         {"code": "HACK", "name": "IV Two Renamed", "currency": "BHD", "cr_number": "12345-1"})
        c.refresh_from_db()
        self.assertEqual((c.code, c.name, c.cr_number), ("IV2", "IV Two Renamed", "12345-1"))

    def test_duplicate_code_or_name_is_a_form_error(self):
        self.post_new()
        self.assertEqual(self.post_new(name="Another").status_code, 200)       # same code
        self.assertEqual(self.post_new(code="ZZ", name="iv two").status_code, 200)   # same name
        self.assertEqual(Company.objects.filter(code__in=["IV2", "ZZ"]).count(), 1)

    def test_non_superuser_creator_gets_access_and_sees_only_their_companies(self):
        perms = Permission.objects.filter(content_type__app_label="organization",
                                          content_type__model="company",
                                          codename__in=["view_company", "add_company"])
        self.hr_user.user_permissions.add(*perms)
        Company.objects.create(code="X9", name="Hidden Co")
        self.client.force_login(self.hr_user)
        self.post_new()
        self.assertTrue(CompanyAccess.objects.filter(user=self.hr_user, company__code="IV2").exists())
        page = self.client.get(reverse("web:company_list"))
        self.assertContains(page, "IV Two")
        self.assertContains(page, "Company One")
        self.assertNotContains(page, "Hidden Co")

    def test_deactivate_is_blocked_while_staff_are_active(self):
        self.client.post(reverse("web:company_toggle", args=[self.co.pk]))
        self.co.refresh_from_db()
        self.assertTrue(self.co.is_active)

        empty = Company.objects.create(code="E1", name="Empty Co")
        url = reverse("web:company_toggle", args=[empty.pk])
        self.client.post(url)
        empty.refresh_from_db()
        self.assertFalse(empty.is_active)
        self.client.post(url)
        empty.refresh_from_db()
        self.assertTrue(empty.is_active)

    def test_company_with_records_cannot_be_deleted(self):
        url = reverse("web:company_delete", args=[self.co.pk])
        self.assertContains(self.client.get(url), "cannot be deleted")
        self.client.post(url, {"confirm_code": self.co.code})
        self.assertTrue(Company.objects.filter(pk=self.co.pk).exists())

    def test_unused_company_is_deleted_together_with_its_setup(self):
        co = Company.objects.create(code="TMP", name="Temp Co")
        head = Department.objects.create(company=co, code="A", name="Head")
        Department.objects.create(company=co, code="B", name="Child", parent=head)
        Shift.objects.create(company=co, code="GEN", name="General", start_time=time(8, 0), end_time=time(17, 0))
        Holiday.objects.create(company=co, date=date(2027, 1, 1), name="New Year")
        ApprovalFlow.objects.create(company=co, code="leave.request", name="Temp flow")
        call_command("seed_phase2", verbosity=0)                    # adds the company's leave types
        self.assertTrue(LeaveType.objects.filter(company=co).exists())

        url = reverse("web:company_delete", args=[co.pk])
        self.client.post(url, {"confirm_code": "wrong"})
        self.assertTrue(Company.objects.filter(pk=co.pk).exists())

        self.assertRedirects(self.client.post(url, {"confirm_code": "tmp"}), reverse("web:company_list"))
        self.assertFalse(Company.objects.filter(pk=co.pk).exists())
        self.assertFalse(Department.objects.filter(company_id=co.pk).exists())
        self.assertFalse(LeaveType.objects.filter(company_id=co.pk).exists())
        self.assertTrue(AuditEvent.objects.filter(module="organization", action="delete",
                                                  object_id=str(co.pk)).exists())