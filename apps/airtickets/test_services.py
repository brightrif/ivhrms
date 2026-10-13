from datetime import timedelta
from decimal import Decimal
from io import StringIO

from django import forms
from django.core.management import call_command
from django.test import SimpleTestCase

from apps.employees.models import Employee
from apps.organization.models import Company
from apps.web.forms.nationality import NationalityInput, NationalityMixin

from . import services
from .models import TicketEntitlement, TicketPolicy
from .nationalities import canonical, key
from .testing import AirCase


class NationalityTests(SimpleTestCase):
    def test_the_many_ways_of_writing_it_become_one(self):
        for typed, expected in [("india", "Indian"), ("INDIA", "Indian"), (" Indian ", "Indian"),
                                ("Phillipino", "Filipino"), ("U.S.A", "American"), ("Sri-Lankan", "Sri Lankan"),
                                ("UAE", "Emirati"), ("bahrain", "Bahraini")]:
            self.assertEqual(canonical(typed), expected, typed)

    def test_what_is_not_in_the_list_is_not_recognised(self):
        for typed in ("", "   ", "Klingon", None):
            self.assertIsNone(canonical(typed))
        self.assertEqual(key(" klingon "), key("KLINGON"))                       # still comparable

    def test_the_form_tidies_what_was_typed_and_suggests_the_known_ones(self):
        class F(NationalityMixin, forms.Form):
            nationality = forms.CharField(required=False, widget=NationalityInput())

        form = F({"nationality": " india "})
        self.assertTrue(form.is_valid())
        self.assertEqual(form.cleaned_data["nationality"], "Indian")
        unknown = F({"nationality": "Klingon  Empire"})
        self.assertTrue(unknown.is_valid())
        self.assertEqual(unknown.cleaned_data["nationality"], "Klingon Empire")           # kept, tidied, not refused
        html = str(F()["nationality"])
        for text in ('list="nationality-options"', '<datalist id="nationality-options">', '<option value="Indian">'):
            self.assertIn(text, html)


class PolicyChoiceTests(AirCase):
    def test_the_most_specific_policy_wins(self):
        anyone = self.policy(name="Everyone", nationalities=[])
        indian = self.policy(name="Indian staff")
        senior = self.policy(name="Indian managers", min_grade=self.manager)
        self.assertEqual(services.policy_for(self.emp), indian)                 # an officer is not senior enough
        self.set_emp(self.emp, grade=self.manager)
        self.assertEqual(services.policy_for(self.emp), senior)
        self.assertEqual(services.policy_for(self.emp2), anyone)                # the Bahraini gets the general one

    def test_priority_breaks_a_tie_and_switched_off_policies_are_ignored(self):
        a, b = self.policy(name="A"), self.policy(name="B", priority=5)
        self.assertEqual(services.policy_for(self.emp), b)
        b.is_active = False
        b.save()
        self.assertEqual(services.policy_for(self.emp), a)

    def test_the_spelling_of_the_nationality_does_not_matter(self):
        indian = self.policy()
        self.set_emp(self.emp, nationality="india ")
        self.assertEqual(services.policy_for(self.emp), indian)

    def test_nobody_else_s_policy_is_ever_used(self):
        other = Company.objects.create(code="OT", name="Other Co")
        TicketPolicy.objects.create(company=other, name="Theirs", nationalities=[])
        self.assertIsNone(services.policy_for(self.emp))

    def test_the_reasons_a_policy_does_not_fit(self):
        policy = self.policy(nationalities=["Pakistani"], min_grade=self.manager, employment_types=["contract"])
        reasons = " | ".join(policy.mismatches(self.emp))
        for text in ("nationality (Indian) is not covered", "grade Officer is below Manager",
                     "contract type (permanent) is not covered"):
            self.assertIn(text, reasons)
        self.assertEqual(self.policy(name="Fits").mismatches(self.emp), [])


class EntitlementTests(AirCase):
    def test_the_first_ticket_comes_after_the_months_of_service(self):
        self.policy()
        ent = services.sync_entitlement(self.emp)
        self.assertEqual((ent.cycle, ent.status, ent.eligible_from),
                         (1, "open", services.add_months(self.emp.joining_date, 12)))
        self.assertEqual(services.entitlement_state(ent)[0], "eligible")           # joined 400 days ago
        self.set_emp(self.emp, joining_date=self.today - timedelta(days=100))
        ent = services.sync_entitlement(self.emp)
        self.assertEqual(services.entitlement_state(ent)[0], "upcoming")
        self.assertEqual(TicketEntitlement.objects.filter(employee=self.emp).count(), 1)   # moved, not duplicated

    def test_syncing_again_changes_nothing(self):
        self.policy()
        first = services.sync_entitlement(self.emp)
        again = services.sync_entitlement(self.emp)
        self.assertEqual((first.pk, TicketEntitlement.objects.count()), (again.pk, 1))

    def test_the_open_ticket_follows_the_policy_until_it_is_taken(self):
        policy = self.policy()
        services.sync_entitlement(self.emp)
        policy.mode, policy.allowance_amount, policy.spouse_covered = "allowance", Decimal("120"), False
        policy.save()
        ent = services.sync_entitlement(self.emp)
        self.assertEqual((ent.mode, ent.allowance_amount, ent.spouse_covered), ("allowance", Decimal("120"), False))

    def test_no_policy_means_no_entitlement(self):
        self.policy(nationalities=["Pakistani"])
        self.assertIsNone(services.sync_entitlement(self.emp))
        policy = self.policy(name="Indian")
        self.assertIsNotNone(services.sync_entitlement(self.emp))
        policy.is_active = False
        policy.save()
        self.assertIsNone(services.sync_entitlement(self.emp))                     # the worked-out row goes
        self.assertFalse(TicketEntitlement.objects.exists())

    def test_labor_workers_and_leavers_are_not_worked_out(self):
        self.policy()
        self.set_emp(self.emp, worker_type="labor")
        self.assertIsNone(services.sync_entitlement(self.emp))
        self.set_emp(self.emp, worker_type="staff", status="separated")
        self.assertIsNone(services.sync_entitlement(self.emp))

    def test_sync_all_counts_and_can_be_limited_to_companies(self):
        self.policy()
        stats = services.sync_all([self.co])
        staff = Employee.objects.filter(company=self.co, worker_type="staff").exclude(status="separated").count()
        self.assertEqual((stats["checked"], stats["with_policy"] + stats["without_policy"]), (staff, staff))
        self.assertEqual(stats["with_policy"], TicketEntitlement.objects.filter(status="open").count())
        self.assertEqual(services.sync_all([Company.objects.create(code="OT", name="Other Co")])["checked"], 0)

    def test_nationality_problems_are_counted(self):
        self.set_emp(self.emp2, nationality="Klingon")
        blank, unknown = services.nationality_problems(Employee.objects.filter(company=self.co))
        self.assertIn(("Klingon", 1), unknown)
        self.assertEqual(blank, Employee.objects.filter(company=self.co, nationality="").count())


class PastTicketTests(AirCase):
    def test_a_ticket_taken_closes_the_open_one_and_moves_the_next(self):
        self.policy(interval_months=24)
        taken = services.record_past_ticket(self.emp, travel_date=self.today - timedelta(days=200),
                                            destination="Cochin (COK)", cost=Decimal("180.500"))
        self.assertEqual((taken.status, taken.cycle, taken.destination, taken.cost),
                         ("used", 1, "Cochin (COK)", Decimal("180.500")))
        nxt = TicketEntitlement.objects.get(employee=self.emp, status="open")
        self.assertEqual((nxt.cycle, nxt.eligible_from), (2, services.add_months(taken.used_on, 24)))

    def test_tickets_are_entered_oldest_first(self):
        self.policy()
        services.record_past_ticket(self.emp, travel_date=self.today - timedelta(days=300))
        with self.assertRaisesMessage(services.TicketError, "oldest first"):
            services.record_past_ticket(self.emp, travel_date=self.today - timedelta(days=350))

    def test_the_date_and_the_cost_are_checked(self):
        self.policy()
        with self.assertRaisesMessage(services.TicketError, "future"):
            services.record_past_ticket(self.emp, travel_date=self.today + timedelta(days=1))
        with self.assertRaisesMessage(services.TicketError, "before"):
            services.record_past_ticket(self.emp, travel_date=self.today - timedelta(days=500))
        with self.assertRaisesMessage(services.TicketError, "negative"):
            services.record_past_ticket(self.emp, travel_date=self.today, cost=Decimal("-1"))
        self.assertFalse(TicketEntitlement.objects.exclude(status="open").exists())

    def test_nothing_can_be_recorded_without_a_policy(self):
        with self.assertRaisesMessage(services.TicketError, "No ticket policy applies"):
            services.record_past_ticket(self.emp, travel_date=self.today)


class CommandTests(AirCase):
    def test_nationalities_are_tidied_only_when_asked(self):
        a = self.set_emp(self.emp, nationality="india")
        b = self.set_emp(self.emp2, nationality="Klingon")
        out = StringIO()
        call_command("normalize_nationalities", stdout=out)
        self.assertIn("Would change", out.getvalue())
        self.assertIn("Klingon", out.getvalue())
        a.refresh_from_db()
        self.assertEqual(a.nationality, "india")                                    # nothing happened yet
        call_command("normalize_nationalities", "--apply", stdout=StringIO())
        a.refresh_from_db()
        b.refresh_from_db()
        self.assertEqual((a.nationality, b.nationality), ("Indian", "Klingon"))      # unknown ones are left alone

    def test_sync_command(self):
        self.policy()
        out = StringIO()
        call_command("sync_air_tickets", stdout=out)
        self.assertIn("with a ticket policy", out.getvalue())
        self.assertTrue(TicketEntitlement.objects.filter(employee=self.emp).exists())

    def test_the_daily_job_is_scheduled_automatically(self):
        try:
            from config.celery import app
        except ModuleNotFoundError:
            self.skipTest("Celery is not installed")
        entry = app.conf.beat_schedule["airtickets-daily-sync"]
        self.assertEqual(entry["task"], "apps.airtickets.tasks.daily_sync")
        app.loader.import_default_modules()
        self.assertIn(entry["task"], app.tasks)
