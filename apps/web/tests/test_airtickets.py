from datetime import timedelta
from decimal import Decimal

from django.urls import reverse

from apps.airtickets import services
from apps.airtickets.models import TicketEntitlement, TicketPolicy, TravelProfile
from apps.airtickets.testing import AirCase


class AirPageCase(AirCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.hr)

    def url(self, name, *args):
        return reverse(f"web:{name}", args=args)

    def policy_post(self, **over):
        data = {"company": self.co.pk, "name": "Pakistani staff", "nationalities": ["Pakistani", "Indian"],
                "interval_months": 12, "first_after_months": 12, "spouse_covered": "on", "max_children": 2,
                "mode": "company", "priority": 0, "is_active": "on"}
        data.update(over)
        return data


class AccessTests(AirPageCase):
    def test_who_can_look_and_who_can_change(self):
        pages = {"list": self.url("employee_tickets"), "policies": self.url("employee_tickets_policies"),
                 "add policy": self.url("employee_tickets_policy_add"),
                 "detail": self.url("employee_tickets_detail", self.emp.pk),
                 "profile": self.url("employee_tickets_profile", self.emp.pk),
                 "past": self.url("employee_tickets_past", self.emp.pk)}
        order = list(pages)
        expected = {self.hr: (200, 200, 200, 200, 200, 200), self.finance: (200, 200, 403, 200, 403, 403),
                    self.boss: (200, 200, 403, 200, 403, 403), self.nobody: (403,) * 6,
                    self.other_hr: (200, 200, 200, 404, 404, 404)}
        self.client.logout()
        self.assertEqual(self.client.get(pages["list"]).status_code, 302)
        for user, statuses in expected.items():
            self.client.force_login(user)
            for name, status in zip(order, statuses):
                self.assertEqual(self.client.get(pages[name]).status_code, status, f"{user.username}: {name}")

    def test_the_tab_is_shown_only_to_people_who_may_use_it(self):
        self.assertContains(self.client.get(self.url("employee_tickets")), "Air Tickets")
        self.client.force_login(self.other_hr)
        self.assertNotContains(self.client.get(self.url("employee_tickets")), "Ali Test")      # someone else's staff


class EligibilityListTests(AirPageCase):
    def test_the_list_shows_who_is_eligible_and_why_others_are_not(self):
        self.policy()
        services.sync_all([self.co])
        r = self.client.get(self.url("employee_tickets"))
        self.assertContains(r, "Ali Test")
        self.assertContains(r, "Eligible now")
        self.assertContains(r, "Expat staff")
        r = self.client.get(self.url("employee_tickets"), {"show": "nopolicy"})
        self.assertContains(r, "Sara Test")                                       # a Bahraini: no policy covers them
        self.assertNotContains(r, "Ali Test")
        r = self.client.get(self.url("employee_tickets"), {"show": "eligible"})
        self.assertContains(r, "Ali Test")
        r = self.client.get(self.url("employee_tickets"), {"show": "soon"})
        self.assertNotContains(r, "Ali Test")

    def test_a_nationality_nobody_recognises_is_flagged(self):
        self.set_emp(self.emp2, nationality="Klingon")
        r = self.client.get(self.url("employee_tickets"))
        self.assertContains(r, "not recognised")
        self.assertContains(r, "Klingon")
        r = self.client.get(self.url("employee_tickets"), {"show": "unknown"})
        self.assertContains(r, "Sara Test")
        self.assertNotContains(r, "Ali Test")

    def test_refresh_works_out_everyones_next_ticket(self):
        self.policy()
        self.assertFalse(TicketEntitlement.objects.exists())
        r = self.client.post(self.url("employee_tickets_sync"), follow=True)
        self.assertContains(r, "have a ticket policy")
        self.assertTrue(TicketEntitlement.objects.filter(employee=self.emp, status="open").exists())
        self.assertEqual(self.client.get(self.url("employee_tickets_sync")).status_code, 405)
        self.client.force_login(self.finance)
        self.assertEqual(self.client.post(self.url("employee_tickets_sync")).status_code, 403)


class PolicyPageTests(AirPageCase):
    def test_adding_a_policy_works_out_the_entitlements_straight_away(self):
        r = self.client.post(self.url("employee_tickets_policy_add"), self.policy_post(), follow=True)
        self.assertContains(r, "Pakistani staff")
        policy = TicketPolicy.objects.get()
        self.assertEqual((policy.nationalities, policy.max_children), (["Pakistani", "Indian"], 2))
        self.assertTrue(TicketEntitlement.objects.filter(employee=self.emp, policy=policy).exists())
        self.assertContains(r, "now have an air ticket entitlement")

    def test_the_rules_are_shown_on_the_form(self):
        url = self.url("employee_tickets_policy_add")
        r = self.client.post(url, self.policy_post(mode="allowance"))
        self.assertContains(r, "Enter the allowance amount")
        self.client.post(url, self.policy_post())
        r = self.client.post(url, self.policy_post(name="pakistani STAFF"))
        self.assertContains(r, "already has a policy with this name")
        self.assertEqual(TicketPolicy.objects.count(), 1)
        self.client.post(url, self.policy_post(name="Allowance", mode="allowance", allowance_amount="150.500"))
        self.assertEqual(TicketPolicy.objects.get(name="Allowance").allowance_amount, Decimal("150.500"))
        self.client.post(url, self.policy_post(name="Booked", mode="company", allowance_amount="99"))
        self.assertIsNone(TicketPolicy.objects.get(name="Booked").allowance_amount)         # an amount means nothing here

    def test_editing_keeps_the_company_and_switching_off_removes_the_entitlements(self):
        policy = self.policy()
        services.sync_all([self.co])
        edit = self.url("employee_tickets_policy_edit", policy.pk)
        self.assertNotIn("company", self.client.get(edit).context["form"].fields)
        self.client.post(edit, self.policy_post(name="Expat staff", nationalities=["Indian"], interval_months=24))
        policy.refresh_from_db()
        self.assertEqual((policy.interval_months, policy.company), (24, self.co))
        self.client.post(self.url("employee_tickets_policy_toggle", policy.pk))
        policy.refresh_from_db()
        self.assertFalse(policy.is_active)
        self.assertFalse(TicketEntitlement.objects.filter(employee=self.emp).exists())
        self.assertEqual(self.client.get(self.url("employee_tickets_policy_toggle", policy.pk)).status_code, 405)

    def test_the_policy_list_shows_what_each_one_covers(self):
        self.policy()
        services.sync_all([self.co])
        r = self.client.get(self.url("employee_tickets_policies"))
        for text in ("Expat staff", "Indian", "Every 12 months", "spouse"):
            self.assertContains(r, text)


class EmployeePageTests(AirPageCase):
    def test_the_page_shows_the_next_ticket_and_the_history(self):
        self.policy()
        services.sync_all([self.co])
        r = self.client.get(self.url("employee_tickets_detail", self.emp.pk))
        self.assertContains(r, "Eligible now")
        self.assertContains(r, "Expat staff")

    def test_when_no_policy_fits_the_page_says_why(self):
        self.policy(nationalities=["Pakistani"], name="Pakistani staff")
        r = self.client.get(self.url("employee_tickets_detail", self.emp.pk))
        self.assertContains(r, "No policy applies")
        self.assertContains(r, "nationality (Indian) is not covered")

    def test_travel_details_are_saved(self):
        r = self.client.post(self.url("employee_tickets_profile", self.emp.pk),
                             {"home_country": "India", "destination_airport": "Cochin (COK)", "notes": ""})
        self.assertRedirects(r, self.url("employee_tickets_detail", self.emp.pk))
        self.assertEqual(TravelProfile.objects.get().destination_airport, "Cochin (COK)")
        self.assertContains(self.client.get(self.url("employee_tickets_detail", self.emp.pk)), "Cochin (COK)")

    def test_a_ticket_already_taken_is_recorded_and_errors_are_shown(self):
        url = self.url("employee_tickets_past", self.emp.pk)
        r = self.client.post(url, {"travel_date": (self.today - timedelta(days=100)).isoformat()})
        self.assertContains(r, "No ticket policy applies")
        self.policy()
        r = self.client.post(url, {"travel_date": (self.today + timedelta(days=3)).isoformat()})
        self.assertContains(r, "in the future")
        r = self.client.post(url, {"travel_date": (self.today - timedelta(days=100)).isoformat(),
                                   "destination": "Cochin (COK)", "cost": "180.500"})
        self.assertRedirects(r, self.url("employee_tickets_detail", self.emp.pk))
        page = self.client.get(self.url("employee_tickets_detail", self.emp.pk))
        self.assertContains(page, "Cochin (COK)")
        self.assertContains(page, "180.500")
        self.assertEqual(TicketEntitlement.objects.filter(employee=self.emp).count(), 2)


class LayoutTests(AirPageCase):
    def test_every_form_places_every_field_it_has(self):
        policy = self.policy()
        pages = [self.url("employee_tickets_policy_add"), self.url("employee_tickets_policy_edit", policy.pk),
                 self.url("employee_tickets_profile", self.emp.pk), self.url("employee_tickets_past", self.emp.pk)]
        for url in pages:
            r = self.client.get(url)
            self.assertEqual(r.status_code, 200, url)
            placed = {n for section in r.context["layout"] for row in section["rows"] for n in row}
            self.assertEqual(placed, set(r.context["form"].fields), url)
