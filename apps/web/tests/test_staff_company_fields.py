from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.organization.models import Company, Designation

User = get_user_model()


class CompanyFieldsTests(TestCase):
    def setUp(self):
        self.spare = Company.objects.create(code="ISP", name="IV Spare Parts", is_default=True)
        self.build = Company.objects.create(code="IVC", name="IV Construction")
        Designation.objects.create(name="Manager")                                    # shared by both
        Designation.objects.create(name="Parts Clerk", company=self.spare)
        Designation.objects.create(name="Site Engineer", company=self.build)
        self.root = User.objects.create_user("root")
        self.root.is_superuser = True
        self.root.is_staff = True
        self.root.save()
        self.client.force_login(self.root)

    def test_choosing_a_company_refreshes_the_designations_with_that_companys_own(self):
        page = self.client.get(reverse("web:employee_company_fields"), {"company": self.build.pk})
        self.assertContains(page, "Site Engineer")
        self.assertContains(page, "Manager")
        self.assertNotContains(page, "Parts Clerk")
        page = self.client.get(reverse("web:employee_company_fields"), {"company": self.spare.pk})
        self.assertContains(page, "Parts Clerk")
        self.assertNotContains(page, "Site Engineer")

    def test_the_add_page_swaps_designation_and_department_together(self):
        html = self.client.get(reverse("web:employee_create")).content.decode()
        swapped = html[html.index('id="company-fields"'):]
        self.assertIn('id="id_designation"', swapped)
        self.assertIn('id="id_department"', swapped)
        self.assertEqual(html.count('id="id_designation"'), 1)                    # not also left outside the swapped area
