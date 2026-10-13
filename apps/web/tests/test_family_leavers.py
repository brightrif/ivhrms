from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.accounts.models import User
from apps.audit.models import AuditEvent
from apps.configuration import services as settings_service
from apps.employees.models import Employee
from apps.dependants.visa import VisaPayment

from .test_family_visa import VisaPageCase


def pdf(name="receipt.pdf"):
    return SimpleUploadedFile(name, b"%PDF-1.4 test", content_type="application/pdf")


class LeaverPageTests(VisaPageCase):
    def test_the_list_warns_about_family_visas_whose_sponsor_is_leaving(self):
        self.make_visa()
        self.person("Layla")                                                           # no visa: nothing to cancel
        url = self.url("employee_family")
        self.assertNotContains(self.client.get(url), "still has a visa while the sponsor")
        Employee.objects.filter(pk=self.emp.pk).update(status="on_notice")
        r = self.client.get(url)
        self.assertContains(r, "still has a visa while the sponsor is on notice")
        self.assertContains(r, "On notice")
        r = self.client.get(url, {"leaver": "1"})
        self.assertContains(r, "Yusuf")
        self.assertNotContains(r, "Layla")
        self.assertContains(self.client.get(url), "Layla")

    def test_another_companys_hr_sees_no_warning(self):
        self.make_visa()
        Employee.objects.filter(pk=self.emp.pk).update(status="on_notice")
        self.client.force_login(self.other_hr)
        self.assertNotContains(self.client.get(self.url("employee_family")), "still has a visa while the sponsor")

    def test_once_a_cancellation_is_raised_the_warning_goes(self):
        self.make_visa()
        Employee.objects.filter(pk=self.emp.pk).update(status="on_notice")
        self.client.post(self.url("employee_family_request_add", self.kid.pk),
                         {"kind": "cancellation", "reason": "Sponsor is leaving"})
        self.assertNotContains(self.client.get(self.url("employee_family")), "still has a visa while the sponsor")


class RulesOnThePagesTests(VisaPageCase):
    def test_the_company_rules_are_explained_on_the_request_form(self):
        mona = self.person("Mona", relationship="parent")
        r = self.client.post(self.url("employee_family_request_add", mona.pk), {"kind": "new", "reason": ""})
        self.assertContains(r, "does not put forward parents")
        settings_service.set_value("dependants.allow_parents", True, self.co)
        r = self.client.post(self.url("employee_family_request_add", mona.pk), {"kind": "new", "reason": ""})
        self.assertEqual(r.status_code, 302)

    def test_papers_first_is_explained_on_the_submit_form(self):
        settings_service.set_value("dependants.require_papers", True, self.co)
        req = self.approve(self.ask())
        r = self.client.post(self.url("employee_family_request_submitted", req.pk),
                             {"submitted_on": self.iso(0), "reference": ""})
        self.assertContains(r, "Still missing before this can go to the authority")

    def test_the_settings_tab_exists_and_is_for_people_given_the_right(self):
        url = reverse("web:settings_module", args=["dependants"])
        self.assertEqual(self.client.get(url).status_code, 403)                        # HR has no settings right yet
        root = User.objects.create_user("root", is_superuser=True)
        self.client.force_login(root)
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Family Visas")
        self.assertContains(r, "Parents")


class ReceiptTests(VisaPageCase):
    def pay(self, req, **over):
        data = {"description": "Government fee", "paid_on": self.iso(0), "government_fee": "40.000", "receipt": pdf()}
        data.update(over)
        return self.client.post(self.url("employee_family_request_payment", req.pk), data)

    def test_a_receipt_is_attached_shown_and_every_download_is_audited(self):
        req = self.approve(self.ask())
        self.assertRedirects(self.pay(req), self.url("employee_family_request", req.pk))
        payment = VisaPayment.objects.get()
        self.assertTrue(payment.receipt)
        self.assertContains(self.client.get(self.url("employee_family_request", req.pk)),
                            self.url("employee_family_receipt", payment.pk))
        for params in ({}, {"download": "1"}):
            r = self.client.get(self.url("employee_family_receipt", payment.pk), params)
            self.assertEqual(r.status_code, 200)
            r.close()
        events = AuditEvent.objects.filter(action="download", object_id=str(payment.pk), actor=self.hr)
        self.assertEqual((events.count(), events.first().module, events.first().company_id), (2, "dependants", self.co.pk))

    def test_who_may_open_a_receipt(self):
        req = self.approve(self.ask())
        self.pay(req)
        url = self.url("employee_family_receipt", VisaPayment.objects.get().pk)
        expected = {self.finance: 200, self.boss: 200, self.nobody: 403, self.other_hr: 404}
        for user, status in expected.items():
            self.client.force_login(user)
            r = self.client.get(url)
            self.assertEqual(r.status_code, status, user.username)
            if hasattr(r, "streaming_content"):
                r.close()

    def test_a_payment_without_a_receipt_has_no_download_and_bad_files_are_refused(self):
        req = self.approve(self.ask())
        self.pay(req, receipt="")
        payment = VisaPayment.objects.get()
        self.assertEqual(self.client.get(self.url("employee_family_receipt", payment.pk)).status_code, 404)
        r = self.pay(req, receipt=SimpleUploadedFile("run.exe", b"MZ", content_type="application/octet-stream"))
        self.assertEqual(r.status_code, 200)
        self.assertIn("receipt", r.context["form"].errors)
        self.assertEqual(VisaPayment.objects.count(), 1)
