from django.urls import reverse

from apps.accounts.models import User
from apps.attendance.tests import BaseCase
from apps.audit.models import AuditEvent
from apps.employees.models import Employee
from apps.organization.models import Company, CompanyAccess, Department, Designation


class DepartmentPageTests(BaseCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.hr_user)
        self.url = reverse("web:department_create")

    def test_staff_without_permission_are_blocked(self):
        self.client.force_login(self.emp_user)
        self.assertEqual(self.client.get(reverse("web:department_list")).status_code, 403)

    def test_create_generates_a_code_and_is_audited(self):
        self.assertRedirects(self.client.post(self.url, {"company": self.co.pk, "name": "Human Resources"}),
                             reverse("web:department_list"))
        d = Department.objects.get(name="Human Resources")
        self.assertEqual((d.company, d.code), (self.co, "HR"))
        self.client.post(self.url, {"company": self.co.pk, "name": "Health Reports"})
        self.assertEqual(Department.objects.get(name="Health Reports").code, "HR2")
        self.assertTrue(AuditEvent.objects.filter(module="organization", action="create",
                                                  object_id=str(d.pk)).exists())

    def test_duplicate_names_are_rejected_per_company_only(self):
        Department.objects.create(company=self.co, code="OPS", name="Operations")
        self.assertEqual(self.client.post(self.url, {"company": self.co.pk, "name": "operations"}).status_code, 200)
        self.assertEqual(Department.objects.filter(name__iexact="operations").count(), 1)
        other = Company.objects.create(code="C2", name="Company Two")
        CompanyAccess.objects.create(user=self.hr_user, company=other)
        self.assertRedirects(self.client.post(self.url, {"company": other.pk, "name": "Operations"}),
                             reverse("web:department_list"))

    def test_cannot_add_to_a_company_without_access(self):
        stranger = Company.objects.create(code="X9", name="Hidden Co")
        self.assertEqual(self.client.post(self.url, {"company": stranger.pk, "name": "Ops"}).status_code, 200)
        self.assertFalse(Department.objects.filter(company=stranger).exists())

    def test_edit_keeps_the_code_and_prevents_cycles(self):
        a = Department.objects.create(company=self.co, code="A", name="Alpha")
        b = Department.objects.create(company=self.co, code="B", name="Beta", parent=a)
        edit = reverse("web:department_edit", args=[a.pk])
        r = self.client.post(edit, {"name": "Alpha", "parent": b.pk})        # its own child
        self.assertEqual(r.status_code, 200)
        self.client.post(edit, {"company": 999, "code": "HACK", "name": "Alpha Renamed"})
        a.refresh_from_db()
        self.assertEqual((a.code, a.name, a.company), ("A", "Alpha Renamed", self.co))

    def test_deactivated_department_leaves_the_staff_form(self):
        d = Department.objects.create(company=self.co, code="OPS", name="Operations")
        fields = reverse("web:employee_company_fields")
        self.assertContains(self.client.get(fields, {"company": self.co.pk}), "Operations")
        self.client.post(reverse("web:department_toggle", args=[d.pk]))
        self.assertNotContains(self.client.get(fields, {"company": self.co.pk}), "Operations")

    def test_delete_is_blocked_while_staff_use_it(self):
        used = Department.objects.create(company=self.co, code="OPS", name="Operations")
        free = Department.objects.create(company=self.co, code="TMP", name="Temp")
        Employee.objects.filter(pk=self.emp.pk).update(department=used)
        self.client.force_login(User.objects.create_superuser("root", password="x"))
        self.assertContains(self.client.get(reverse("web:department_delete", args=[used.pk])), "cannot be deleted")
        self.client.post(reverse("web:department_delete", args=[used.pk]))
        self.assertTrue(Department.objects.filter(pk=used.pk).exists())
        self.assertRedirects(self.client.post(reverse("web:department_delete", args=[free.pk])),
                             reverse("web:department_list"))
        self.assertFalse(Department.objects.filter(pk=free.pk).exists())


class DesignationPageTests(BaseCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.hr_user)

    def test_create_duplicate_toggle_and_delete(self):
        url = reverse("web:designation_create")
        self.assertRedirects(self.client.post(url, {"name": "  Site   Engineer "}), reverse("web:designation_list"))
        d = Designation.objects.get()
        self.assertEqual(d.name, "Site Engineer")
        self.assertEqual(self.client.post(url, {"name": "site engineer"}).status_code, 200)
        self.assertEqual(Designation.objects.count(), 1)

        form_page = reverse("web:employee_create")
        option = ">Site Engineer</option>"          # the dropdown entry; flash messages also contain the name
        self.assertContains(self.client.get(form_page), option)
        self.client.post(reverse("web:designation_toggle", args=[d.pk]))
        self.assertNotContains(self.client.get(form_page), option)

        Employee.objects.filter(pk=self.emp.pk).update(designation=d)
        self.client.force_login(User.objects.create_superuser("root", password="x"))
        delete = reverse("web:designation_delete", args=[d.pk])
        self.assertContains(self.client.get(delete), "cannot be deleted")
        Employee.objects.filter(pk=self.emp.pk).update(designation=None)
        self.assertRedirects(self.client.post(delete), reverse("web:designation_list"))
        self.assertFalse(Designation.objects.exists())