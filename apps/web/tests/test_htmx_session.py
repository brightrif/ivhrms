from django.test import TestCase
from django.urls import reverse


class HtmxSessionExpiryTests(TestCase):
    def test_a_background_request_after_logoff_sends_the_whole_page_to_sign_in(self):
        url = reverse("web:approval_count")
        r = self.client.get(url, HTTP_HX_REQUEST="true", HTTP_HX_CURRENT_URL="http://testserver/leave/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["HX-Redirect"], reverse("web:login") + "?next=%2Fleave%2F")

    def test_an_ordinary_request_still_gets_the_normal_redirect(self):
        self.assertEqual(self.client.get(reverse("web:approval_count")).status_code, 302)