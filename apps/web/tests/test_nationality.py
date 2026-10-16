from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from apps.organization.models import Country
from apps.web.forms.nationality import NationalityField


class NationalityFieldTests(TestCase):
    def test_only_countries_switched_on_are_accepted_and_the_name_is_tidied(self):
        field = NationalityField()
        self.assertEqual(field.clean("india"), "India")
        with self.assertRaises(ValidationError):
            field.clean("Egypt")
        Country.objects.filter(name="Egypt").update(is_nationality=True)
        self.assertEqual(field.clean("Egypt"), "Egypt")

    def test_blank_is_fine_and_a_value_already_saved_stays_valid(self):
        field = NationalityField()
        self.assertEqual(field.clean(""), "")
        field.keep = "Indian"
        self.assertEqual(field.clean("indian"), "Indian")
        with self.assertRaises(ValidationError):
            field.clean("Nepali")

    def test_the_box_lists_the_chosen_countries_with_flags(self):
        html = NationalityField().widget.render("nationality", "")
        self.assertIn("India", html)
        self.assertIn("fi-in", html)
        self.assertNotIn("Egypt", html)


class NationalitySettingsTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_superuser("root", password="x"))

    def test_a_country_can_be_added_to_the_nationality_list_without_touching_the_phone_list(self):
        egypt = Country.objects.get(name="Egypt")
        self.client.post(reverse("web:country_toggle", args=[egypt.pk]),
                         {"which": "nationality", f"nationality-{egypt.pk}": "on"})
        egypt.refresh_from_db()
        self.assertTrue(egypt.is_nationality)
        self.assertFalse(egypt.is_active)