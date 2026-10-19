from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.dependants import status_approver as chosen
from apps.organization.models import Company, CompanyAccess


class ApproverPageCase(TestCase):
    def setUp(self):
        self.co = Company.objects.create(code="C1", name="Company One")
        self.root = User.objects.create_superuser("root", password="pw")
        self.investor = User.objects.create_user("investor", password="pw", first_name="Ibrahim", last_name="Owner")
        CompanyAccess.objects.create(user=self.investor, company=self.co)
        self.viewer = User.objects.create_user("viewer", password="pw")
        CompanyAccess.objects.create(user=self.viewer, company=self.co)
        self.viewer.user_permissions.add(Permission.objects.get(content_type__app_label="configuration",
                                                                codename="view_settings_dependants"))
        self.url = reverse("web:family_status_approver")

    def post(self, **over):
        data = {"company": self.co.pk, "approver": self.investor.pk}
        data.update(over)
        return self.client.post(self.url, data)


class PageTests(ApproverPageCase):
    def test_someone_with_no_right_gets_a_403(self):
        self.client.force_login(User.objects.create_user("nobody"))
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_a_viewer_sees_who_approves_but_cannot_change_it(self):
        chosen.set_approver(self.root, self.co, self.investor)
        self.client.force_login(self.viewer)
        resp = self.client.get(self.url)
        self.assertContains(resp, "Ibrahim Owner")
        self.assertNotContains(resp, "Change it")
        self.assertEqual(self.post(approver="").status_code, 403)
        self.assertEqual(chosen.approver_for(self.co), self.investor)

    def test_the_superuser_names_the_approver(self):
        self.client.force_login(self.root)
        self.assertRedirects(self.post(), self.url)
        self.assertEqual(chosen.approver_for(self.co), self.investor)
        self.assertContains(self.client.get(self.url), "Ibrahim Owner")

    def test_leaving_it_on_the_default_goes_back_to_the_reporting_manager(self):
        self.client.force_login(self.root)
        self.post()
        self.assertRedirects(self.post(approver=""), self.url)
        self.assertIsNone(chosen.approver_for(self.co))
        self.assertContains(self.client.get(self.url), "Reporting manager")

    def test_someone_who_cannot_be_chosen_is_refused(self):
        self.client.force_login(self.root)
        stranger = User.objects.create_user("stranger")                       # no company access, no employee record
        self.assertContains(self.post(approver=stranger.pk), "Select a valid choice")
        self.assertIsNone(chosen.approver_for(self.co))

    def test_the_family_visas_tab_shows_it_with_a_link(self):
        chosen.set_approver(self.root, self.co, self.investor)
        self.client.force_login(self.root)
        resp = self.client.get(reverse("web:settings_module", args=["dependants"]))
        self.assertContains(resp, "Ibrahim Owner")
        self.assertContains(resp, reverse("web:family_status_approver"))
