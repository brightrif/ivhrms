from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import Permission

from apps.core import approvals
from apps.core.models import ApprovalFlow
from apps.dependants import services as family

from . import request_services as tickets
from . import services
from .defaults import FLOW_CODE
from .models import TicketEntitlement
from .requestcase import RequestCase
from .ticketrequests import TicketRequest


def fares(req, amount="150.000"):
    return {t.pk: (f"176-{t.pk}", Decimal(amount)) for t in req.travellers.all()}


class FlowTests(RequestCase):
    def test_a_default_flow_exists_after_migrate(self):
        flow = ApprovalFlow.objects.get(code=FLOW_CODE, company__isnull=True)
        steps = list(flow.steps.order_by("order"))
        self.assertEqual([(s.order, s.approver_type) for s in steps], [(1, "manager"), (2, "group")])
        self.assertEqual(steps[1].group.name, "HR")

    def test_group_permissions_exist_after_migrate(self):
        self.assertTrue(self.hr.has_perm("airtickets.add_ticketrequest"))
        self.assertTrue(self.hr.has_perm("airtickets.change_ticketrequest"))
        self.assertTrue(self.finance.has_perm("airtickets.view_ticketrequest"))
        self.assertFalse(self.finance.has_perm("airtickets.add_ticketrequest"))
        self.assertTrue(Permission.objects.filter(codename="view_tickettraveller").exists())


class RequestRulesTests(RequestCase):
    def test_a_request_needs_a_policy_a_staff_member_and_someone_who_has_not_left(self):
        self.pol.is_active = False
        self.pol.save()
        with self.assertRaisesMessage(tickets.RequestError, "No ticket policy applies"):
            self.ask()
        self.pol.is_active = True
        self.pol.save()
        self.set_emp(self.emp, status="separated")
        with self.assertRaisesMessage(tickets.RequestError, "has left"):
            self.ask()
        self.set_emp(self.emp, status="active", worker_type="labor")
        with self.assertRaisesMessage(tickets.RequestError, "for staff"):
            self.ask()
        self.assertFalse(TicketRequest.objects.exists())

    def test_the_dates_are_checked(self):
        with self.assertRaisesMessage(tickets.RequestError, "in the past"):
            self.ask(depart_on=self.today - timedelta(days=1))
        with self.assertRaisesMessage(tickets.RequestError, "must be after the departure"):
            self.ask(return_on=self.today + timedelta(days=30))
        self.set_emp(self.emp, joining_date=self.today - timedelta(days=100))      # not yet due
        with self.assertRaisesMessage(tickets.RequestError, "is due from"):
            self.ask()
        self.assertEqual(self.ask(return_on=None, depart_on=self.today + timedelta(days=400)).return_on, None)

    def test_one_request_at_a_time(self):
        self.ask()
        with self.assertRaisesMessage(tickets.RequestError, "already"):
            self.ask()

    def test_only_the_family_the_policy_covers_can_travel(self):
        with self.assertRaisesMessage(tickets.RequestError, "Mona is not covered"):
            self.ask(dependants=[self.mona])
        with self.assertRaisesMessage(tickets.RequestError, "up to 2 children"):
            self.ask(dependants=[self.yusuf, self.layla, self.omar])
        huda = family.add_dependant(self.hr, self.emp, relationship="spouse", first_name="Huda")
        with self.assertRaisesMessage(tickets.RequestError, "Only one spouse"):
            self.ask(dependants=[self.noor, huda])
        stranger = family.add_dependant(self.hr, self.emp2, relationship="spouse", first_name="Sana")
        with self.assertRaisesMessage(tickets.RequestError, "not covered"):
            self.ask(dependants=[stranger])
        self.pol.spouse_covered = False
        self.pol.save()
        services.sync_entitlement(self.emp)
        with self.assertRaisesMessage(tickets.RequestError, "Noor is not covered"):
            self.ask(dependants=[self.noor])
        self.assertFalse(TicketRequest.objects.exists())

    def test_the_travellers_are_the_employee_and_the_family(self):
        req = self.ask(dependants=[self.noor, self.yusuf])
        self.assertEqual([(t.name, t.relationship) for t in req.travellers.all()],
                         [("Ali Test", "employee"), ("Noor", "spouse"), ("Yusuf", "child")])
        self.assertEqual((req.mode, req.status, req.requested_by), ("company", "pending", self.hr))

    def test_an_allowance_is_worked_out_for_everyone_travelling(self):
        self.to_allowance("100")
        req = self.ask(dependants=[self.noor, self.yusuf])
        self.assertEqual((req.mode, req.allowance_total), ("allowance", Decimal("300.000")))


class ApprovalTests(RequestCase):
    def test_both_approvals_are_needed_and_the_person_who_asked_cannot_give_them(self):
        req = self.ask()
        a = self.approval_of(req)
        approvals.decide(a.pk, self.boss, "approve")
        req.refresh_from_db()
        self.assertEqual(req.status, "pending")
        with self.assertRaises(approvals.ApprovalError):
            approvals.decide(a.pk, self.hr, "approve")
        approvals.decide(a.pk, self.hr_b, "approve")
        req.refresh_from_db()
        self.assertEqual(req.status, "approved")

    def test_a_rejection_frees_the_employee_to_ask_again(self):
        req = self.ask()
        approvals.decide(self.approval_of(req).pk, self.boss, "reject", comment="Busy period")
        req.refresh_from_db()
        self.assertEqual(req.status, "rejected")
        self.assertEqual(self.ask().status, "pending")

    def test_only_the_person_who_asked_can_withdraw(self):
        req = self.ask()
        with self.assertRaises(tickets.RequestError):
            tickets.withdraw(req, self.hr_b)
        self.assertEqual(tickets.withdraw(req, self.hr).status, "cancelled")
        with self.assertRaises(tickets.RequestError):
            tickets.withdraw(req, self.hr)

    def test_finishing_the_approval_twice_changes_nothing(self):
        req = self.approve(self.ask())
        tickets.issue(req, self.hr, issued_on=self.today, tickets=fares(req))
        tickets.finalize(self.approval_of(req))
        req.refresh_from_db()
        self.assertEqual(req.status, "booked")

    def test_a_policy_switched_off_does_not_remove_the_ticket_a_request_depends_on(self):
        req = self.approve(self.ask())
        self.pol.is_active = False
        self.pol.save()
        self.assertIsNone(services.sync_entitlement(self.emp))
        self.assertTrue(TicketEntitlement.objects.filter(pk=req.entitlement_id).exists())


class IssueTests(RequestCase):
    def test_the_tickets_are_recorded_with_their_numbers_and_fares(self):
        req = self.ask(dependants=[self.noor])
        with self.assertRaisesMessage(tickets.RequestError, "approved"):
            tickets.issue(req, self.hr, issued_on=self.today, tickets=fares(req))
        req = self.approve(req)
        partial = fares(req)
        partial.popitem()
        with self.assertRaisesMessage(tickets.RequestError, "Enter the ticket number and the fare"):
            tickets.issue(req, self.hr, issued_on=self.today, tickets=partial)
        with self.assertRaisesMessage(tickets.RequestError, "future"):
            tickets.issue(req, self.hr, issued_on=self.today + timedelta(days=1), tickets=fares(req))
        with self.assertRaisesMessage(tickets.RequestError, "more than the fares"):
            tickets.issue(req, self.hr, issued_on=self.today, tickets=fares(req), employee_share=Decimal("301"))
        req = tickets.issue(req, self.hr, airline="Gulf Air", booking_ref="ABC123", issued_on=self.today,
                            tickets=fares(req), employee_share=Decimal("20"))
        self.assertEqual((req.status, req.airline, req.total_fare, req.company_cost),
                         ("booked", "Gulf Air", Decimal("300.000"), Decimal("280.000")))
        self.assertTrue(all(t.ticket_no for t in req.travellers.all()))

    def test_an_allowance_is_not_issued_as_tickets(self):
        self.to_allowance()
        req = self.approve(self.ask())
        with self.assertRaisesMessage(tickets.RequestError, "allowance"):
            tickets.issue(req, self.hr, issued_on=self.today, tickets=fares(req))


class TravelTests(RequestCase):
    def booked(self):
        req = self.approve(self.ask(dependants=[self.noor]))
        return tickets.issue(req, self.hr, issued_on=self.today, tickets=fares(req), employee_share=Decimal("20"))

    def test_travelling_closes_the_ticket_and_works_out_the_next(self):
        req = self.booked()
        departed = self.today - timedelta(days=1)
        req = tickets.mark_travelled(req, departed_on=departed, returned_on=self.today)
        self.assertEqual((req.status, req.departed_on), ("completed", departed))
        taken = TicketEntitlement.objects.get(pk=req.entitlement_id)
        self.assertEqual((taken.status, taken.used_on, taken.cost, taken.destination),
                         ("used", departed, Decimal("280.000"), "Cochin (COK)"))
        nxt = TicketEntitlement.objects.get(employee=self.emp, status="open")
        self.assertEqual((nxt.cycle, nxt.eligible_from), (taken.cycle + 1, services.add_months(departed, 12)))
        with self.assertRaisesMessage(tickets.RequestError, "is due from"):                  # not due again yet
            self.ask()

    def test_the_rules_for_marking_travelled(self):
        req = self.approve(self.ask())
        with self.assertRaisesMessage(tickets.RequestError, "issued"):
            tickets.mark_travelled(req, departed_on=self.today)
        req = tickets.issue(req, self.hr, issued_on=self.today, tickets=fares(req))
        with self.assertRaisesMessage(tickets.RequestError, "future"):
            tickets.mark_travelled(req, departed_on=self.today + timedelta(days=1))
        with self.assertRaisesMessage(tickets.RequestError, "before the departure"):
            tickets.mark_travelled(req, departed_on=self.today, returned_on=self.today - timedelta(days=1))

    def test_a_ticket_recorded_by_hand_in_the_meantime_is_not_closed_twice(self):
        req = self.booked()
        services.record_past_ticket(self.emp, travel_date=self.today - timedelta(days=10))
        with self.assertRaisesMessage(tickets.RequestError, "already marked as taken"):
            tickets.mark_travelled(req, departed_on=self.today)
        req.refresh_from_db()
        self.assertEqual(req.status, "booked")


class AllowanceTests(RequestCase):
    def test_paying_the_allowance_closes_the_ticket_and_works_out_the_next(self):
        self.to_allowance("100")
        req = self.approve(self.ask(dependants=[self.yusuf]))
        req = tickets.pay_allowance(req, paid_on=self.today, reference="PAY-7")
        self.assertEqual((req.status, req.paid_on, req.payment_ref), ("paid", self.today, "PAY-7"))
        taken = TicketEntitlement.objects.get(pk=req.entitlement_id)
        self.assertEqual((taken.status, taken.cost), ("paid", Decimal("200.000")))
        nxt = TicketEntitlement.objects.get(employee=self.emp, status="open")
        self.assertEqual(nxt.eligible_from, services.add_months(self.today, 12))

    def test_a_company_booked_request_is_not_paid_as_an_allowance(self):
        req = self.approve(self.ask())
        with self.assertRaisesMessage(tickets.RequestError, "booked by the company"):
            tickets.pay_allowance(req, paid_on=self.today)

    def test_an_allowance_cannot_be_paid_early_in_the_future_or_for_nothing(self):
        self.to_allowance("100")
        req = self.ask()
        with self.assertRaisesMessage(tickets.RequestError, "approved"):
            tickets.pay_allowance(req, paid_on=self.today)
        req = self.approve(req)
        with self.assertRaisesMessage(tickets.RequestError, "future"):
            tickets.pay_allowance(req, paid_on=self.today + timedelta(days=1))
        with self.assertRaisesMessage(tickets.RequestError, "Enter the amount"):
            tickets.pay_allowance(req, paid_on=self.today, amount=Decimal("0"))


class CancelTests(RequestCase):
    def test_cancelling_keeps_the_ticket_open_so_the_employee_can_ask_again(self):
        req = self.approve(self.ask())
        with self.assertRaisesMessage(tickets.RequestError, "reason"):
            tickets.cancel(req, self.hr, reason="  ")
        req = tickets.cancel(req, self.hr, reason="Plans changed")
        self.assertEqual((req.status, req.cancel_reason), ("cancelled", "Plans changed"))
        self.assertEqual(TicketEntitlement.objects.get(pk=req.entitlement_id).status, "open")
        self.assertEqual(self.ask().status, "pending")

    def test_issued_tickets_can_be_cancelled_but_a_trip_that_happened_cannot(self):
        req = self.approve(self.ask())
        req = tickets.issue(req, self.hr, issued_on=self.today, tickets=fares(req))
        self.assertEqual(tickets.cancel(req, self.hr, reason="Flight cancelled").status, "cancelled")
        req2 = self.approve(self.ask())
        req2 = tickets.issue(req2, self.hr, issued_on=self.today, tickets=fares(req2))
        req2 = tickets.mark_travelled(req2, departed_on=self.today)
        with self.assertRaises(tickets.RequestError):
            tickets.cancel(req2, self.hr, reason="Too late")
