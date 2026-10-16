from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from apps.organization.models import Country
from apps.web.forms.phone import phone_field, whatsapp_field


class PhoneFieldTests(TestCase):
    def test_the_code_and_the_number_are_joined(self):
        self.assertEqual(phone_field().clean({"code": "973", "number": "3336 6235"}), "+973 33366235")
        self.assertEqual(whatsapp_field().clean({"code": "91", "number": "98765 43210"}), "919876543210")

    def test_a_typed_international_number_picks_its_own_country(self):
        self.assertEqual(phone_field().clean({"code": "973", "number": "+91 98765 43210"}), "+91 9876543210")

    def test_a_leading_zero_and_a_repeated_code_are_dropped(self):
        self.assertEqual(phone_field().clean({"code": "880", "number": "01712 345678"}), "+880 1712345678")
        self.assertEqual(phone_field().clean({"code": "973", "number": "97333366235"}), "+973 33366235")

    def test_a_country_that_is_switched_off_is_not_accepted_in_a_typed_number(self):
        with self.assertRaises(ValidationError):
            phone_field().clean({"code": "973", "number": "+92 300 1234567"})
        Country.objects.filter(dial_code="92").update(is_active=True)
        self.assertEqual(phone_field().clean({"code": "973", "number": "+92 300 1234567"}), "+92 3001234567")

    def test_blank_stays_blank_and_a_single_old_style_value_still_works(self):
        self.assertEqual(phone_field().clean({"code": "973", "number": ""}), "")
        self.assertIsNone(whatsapp_field().clean({"code": "973", "number": ""}))
        self.assertEqual(whatsapp_field().clean("+973 3336 6235"), "97333366235")

    def test_the_box_shows_the_flag_and_the_code(self):
        html = phone_field().widget.render("phone", "+973 33366235")
        self.assertIn("fi-bh", html)
        self.assertIn("+973", html)


class CountrySettingsTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_superuser("root", password="x"))

    def test_a_country_can_be_switched_on_but_the_last_one_cannot_be_switched_off(self):
        pk = Country.objects.get(dial_code="92").pk
        self.client.post(reverse("web:country_toggle", args=[pk]), {f"country-{pk}": "on"})
        self.assertTrue(Country.objects.get(pk=pk).is_active)
        bahrain = Country.objects.get(dial_code="973")
        for other in Country.objects.filter(is_active=True).exclude(pk=bahrain.pk):
            self.client.post(reverse("web:country_toggle", args=[other.pk]), {})
        r = self.client.post(reverse("web:country_toggle", args=[bahrain.pk]), {})
        self.assertEqual(r.status_code, 409)
        self.assertTrue(Country.objects.get(pk=bahrain.pk).is_active)

    def test_a_new_country_is_added_switched_on_and_duplicates_are_refused(self):
        add = reverse("web:country_add")
        self.client.post(add, {"new_country_name": "Nepal", "new_country_code": "+977", "new_country_iso": "np"})
        self.assertEqual(Country.objects.get(dial_code="977").iso, "np")
        r = self.client.post(add, {"new_country_name": "Nepal Two", "new_country_code": "977", "new_country_iso": "np"})
        self.assertContains(r, "already")
        r = self.client.post(add, {"new_country_name": "Atlantis", "new_country_code": "999", "new_country_iso": "zz"})
        self.assertContains(r, "flag")
        self.assertEqual(Country.objects.filter(dial_code="977").count(), 1)