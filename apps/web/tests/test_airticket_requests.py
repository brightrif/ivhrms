from datetime import timedelta

from django.urls import reverse

from apps.accounts.models import User
from apps.airtickets import request_services as tickets
from apps.airtickets import services
from apps.airtickets.models import TicketEntitlement
from apps.airtickets.requestcase import RequestCase
from apps.airtickets.ticketrequests import TicketRequest
from apps.core import approvals
from apps.employees.models import Employee


class RequestPageCase(RequestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.hr)

    def url(self, name, *args):
        return reverse(f"web:{name}", args=args)

    def trip(self, **over):
        data = {"depart_on": self.iso(30), "return_on": self.iso(60), "destination": "Cochin (COK)", "notes": ""}
        data.update(over)
        return data

    def issue_data(self, req, **over):
        data = {"airline": "Gulf Air", "booking_ref": "ABC123", "issued_on": self.iso(0), "employee_share": ""}
        for t in req.travellers.all():
            data[f"ticket_{t.pk}"] = f"176-{t.pk}"
            data[f"fare_{t.pk}"] = "150.000"
        data.update(over)
        return data


class AccessTests(RequestPageCase):
    def test_who_can_look_ask_and_process(self):
        req = self.approve(self.ask())
        pages = {"list": self.url("employee_tickets_requests"),
                 "detail": self.url("employee_tickets_request", req.pk),
                 "ask": self.url("employee_tickets_request_add", self.emp.pk),
                 "issue": self.url("employee_tickets_request_issue", req.pk),
                 "travelled": self.url("employee_tickets_request_travelled", req.pk),
                 "paid": self.url("employee_tickets_request_paid", req.pk),
                 "cancel": self.url("employee_tickets_request_cancel", req.pk)}
        order = list(pages)
        expected = {self.hr: (200,) * 7,
                    self.finance: (200, 200, 403, 403, 403, 403, 403),
                    self.boss: (200, 200, 403, 403, 403, 403, 403),
                    self.nobody: (403,) * 7,
                    self.other_hr: (200, 404, 404, 404, 404, 404, 404)}
        self.client.logout()
        self.assertEqual(self.client.get(pages["list"]).status_code, 302)
        for user, statuses in expected.items():
            self.client.force_login(user)
            for name, status in zip(order, statuses):
                self.assertEqual(self.client.get(pages[name]).status_code, status, f"{user.username}: {name}")


class HrFlowTests(RequestPageCase):
    def test_hr_asks_for_an_employee_the_approvers_decide_and_hr_issues_the_tickets(self):
        r = self.client.post(self.url("employee_tickets_request_add", self.emp.pk),
                             {**self.trip(), "travellers": [self.noor.pk, self.yusuf.pk]})
        req = TicketRequest.objects.get()
        self.assertRedirects(r, self.url("employee_tickets_request", req.pk))
        self.assertEqual(req.travellers.count(), 3)
        page = self.client.get(self.url("employee_tickets_request", req.pk))
        self.assertContains(page, "Reporting manager")
        self.assertContains(page, "Waiting for this approval")

        decide = self.url("approval_decide", self.approval_of(req).pk)
        self.client.force_login(self.boss)
        self.assertContains(self.client.get(self.url("approvals")), "Air ticket: Ali Test")
        self.assertEqual(self.client.post(decide, {"decision": "approve"}).status_code, 200)
        self.client.force_login(self.hr_b)
        self.assertEqual(self.client.post(decide, {"decision": "approve"}).status_code, 200)
        req.refresh_from_db()
        self.assertEqual(req.status, "approved")

        self.client.force_login(self.hr)
        self.assertContains(self.client.get(self.url("employee_tickets_request", req.pk)), "Issue the tickets")
        r = self.client.post(self.url("employee_tickets_request_issue", req.pk), self.issue_data(req))
        self.assertRedirects(r, self.url("employee_tickets_request", req.pk))
        page = self.client.get(self.url("employee_tickets_request", req.pk))
        for text in ("Gulf Air", "ABC123", "450.000", "Mark as travelled"):
            self.assertContains(page, text)

        r = self.client.post(self.url("employee_tickets_request_travelled", req.pk),
                             {"departed_on": self.iso(0), "returned_on": ""})
        self.assertRedirects(r, self.url("employee_tickets_request", req.pk))
        req.refresh_from_db()
        self.assertEqual(req.status, "completed")
        self.assertEqual(TicketEntitlement.objects.get(employee=self.emp, status="open").cycle, 2)
        self.assertContains(self.client.get(self.url("employee_tickets_detail", self.emp.pk)), "Travelled")

    def test_errors_are_shown_on_the_forms(self):
        add = self.url("employee_tickets_request_add", self.emp.pk)
        r = self.client.post(add, self.trip(depart_on=self.iso(-3)))
        self.assertContains(r, "cannot be in the past")
        r = self.client.post(add, {**self.trip(), "travellers": [self.yusuf.pk, self.layla.pk, self.omar.pk]})
        self.assertContains(r, "up to 2 children")
        self.assertFalse(TicketRequest.objects.exists())
        req = self.ask()
        r = self.client.post(self.url("employee_tickets_request_issue", req.pk), self.issue_data(req))
        self.assertContains(r, "approved")

    def test_a_missing_ticket_number_is_asked_for_by_name(self):
        req = self.approve(self.ask(dependants=[self.noor]))
        noor = req.travellers.get(name="Noor")
        r = self.client.post(self.url("employee_tickets_request_issue", req.pk),
                             self.issue_data(req, **{f"ticket_{noor.pk}": ""}))
        self.assertEqual(r.status_code, 200)
        self.assertIn(f"ticket_{noor.pk}", r.context["form"].errors)

    def test_an_allowance_is_recorded_as_paid(self):
        self.to_allowance("100")
        req = self.approve(self.ask(dependants=[self.noor]))
        page = self.client.get(self.url("employee_tickets_request", req.pk))
        self.assertContains(page, "Record the allowance paid")
        self.assertNotContains(page, "Issue the tickets")
        r = self.client.post(self.url("employee_tickets_request_paid", req.pk),
                             {"paid_on": self.iso(0), "amount": "200.000", "reference": "PAY-1"})
        self.assertRedirects(r, self.url("employee_tickets_request", req.pk))
        req.refresh_from_db()
        self.assertEqual((req.status, req.payment_ref), ("paid", "PAY-1"))

    def test_cancelling_needs_a_reason_and_leaves_the_ticket_open(self):
        req = self.approve(self.ask())
        url = self.url("employee_tickets_request_cancel", req.pk)
        self.assertContains(self.client.post(url, {"reason": ""}), "This field is required")
        self.assertRedirects(self.client.post(url, {"reason": "Plans changed"}),
                             self.url("employee_tickets_request", req.pk))
        req.refresh_from_db()
        self.assertEqual(req.status, "cancelled")
        self.assertEqual(TicketEntitlement.objects.get(pk=req.entitlement_id).status, "open")

    def test_only_the_person_who_asked_can_withdraw(self):
        req = self.ask()
        url = self.url("employee_tickets_request_withdraw", req.pk)
        self.assertEqual(self.client.get(url).status_code, 405)
        self.client.force_login(self.hr_b)
        self.client.post(url)
        req.refresh_from_db()
        self.assertEqual(req.status, "pending")
        self.client.force_login(self.hr)
        self.client.post(url)
        req.refresh_from_db()
        self.assertEqual(req.status, "cancelled")

    def test_the_list_filters_by_status(self):
        done = self.approve(self.ask())
        tickets.cancel(done, self.hr, reason="Plans changed")
        self.ask()
        url = self.url("employee_tickets_requests")
        self.assertEqual(len(self.client.get(url).context["page"]), 1)                         # in progress only
        self.assertEqual(len(self.client.get(url, {"status": "cancelled"}).context["page"]), 1)
        self.assertEqual(len(self.client.get(url, {"status": "all"}).context["page"]), 2)

    def test_the_employee_page_offers_a_request_until_one_is_open(self):
        page_url = self.url("employee_tickets_detail", self.emp.pk)
        self.assertContains(self.client.get(page_url), "Request a ticket")
        self.ask()
        r = self.client.get(page_url)
        self.assertNotContains(r, "Request a ticket")
        self.assertContains(r, "Waiting for approval")
        self.client.force_login(self.finance)
        self.assertNotContains(self.client.get(page_url), "Request a ticket")


class SelfServiceTests(RequestPageCase):
    def setUp(self):
        super().setUp()
        self.ali = User.objects.create_user("ali", email="ali@example.com")
        Employee.objects.filter(pk=self.emp.pk).update(user=self.ali)
        self.client.force_login(self.ali)

    def test_an_employee_sees_their_next_ticket_and_asks_for_it(self):
        r = self.client.get(self.url("my_tickets"))
        self.assertContains(r, "My next ticket")
        self.assertContains(r, "You can request it now")
        r = self.client.post(self.url("my_ticket_apply"), {**self.trip(), "travellers": [self.noor.pk]})
        self.assertRedirects(r, self.url("my_tickets"))
        req = TicketRequest.objects.get()
        self.assertEqual((req.requested_by, req.employee, req.travellers.count()), (self.ali, self.emp, 2))
        page = self.client.get(self.url("my_tickets"))
        self.assertContains(page, "Waiting for approval")
        self.assertNotContains(page, "Request my ticket")                       # one at a time

    def test_the_hr_step_can_then_be_given_by_any_hr_user_because_the_employee_asked(self):
        self.client.post(self.url("my_ticket_apply"), self.trip())
        req = TicketRequest.objects.get()
        a = self.approval_of(req)
        approvals.decide(a.pk, self.boss, "approve")
        approvals.decide(a.pk, self.hr, "approve")                              # HR did not ask, so HR may approve
        req.refresh_from_db()
        self.assertEqual(req.status, "approved")
        self.assertContains(self.client.get(self.url("my_tickets")), "Approved")

    def test_an_employee_can_withdraw_only_their_own_waiting_request(self):
        self.client.post(self.url("my_ticket_apply"), self.trip())
        req = TicketRequest.objects.get()
        self.client.post(self.url("my_ticket_withdraw", req.pk))
        req.refresh_from_db()
        self.assertEqual(req.status, "cancelled")
        self.client.force_login(self.nobody)                                   # has no employee record
        self.assertEqual(self.client.post(self.url("my_ticket_withdraw", req.pk)).status_code, 403)

    def test_the_rules_are_shown_to_the_employee(self):
        r = self.client.post(self.url("my_ticket_apply"), self.trip(depart_on=self.iso(-2)))
        self.assertContains(r, "cannot be in the past")
        self.pol.is_active = False
        self.pol.save()
        self.assertContains(self.client.get(self.url("my_tickets")), "No air ticket policy applies to you")
        self.assertRedirects(self.client.get(self.url("my_ticket_apply")), self.url("my_tickets"))


class LayoutTests(RequestPageCase):
    def test_every_form_places_every_field_it_has(self):
        self.client.force_login(self.hr)
        approved = self.approve(self.ask(dependants=[self.noor]))
        pages = [self.url("employee_tickets_request_issue", approved.pk),
                 self.url("employee_tickets_request_travelled", approved.pk),
                 self.url("employee_tickets_request_cancel", approved.pk)]
        for url in pages:
            r = self.client.get(url)
            self.assertEqual(r.status_code, 200, url)
            placed = {n for section in r.context["layout"] for row in section["rows"] for n in row}
            self.assertEqual(placed, set(r.context["form"].fields), url)
        tickets.cancel(approved, self.hr, reason="test")
        self.to_allowance()
        allowance = self.approve(self.ask())
        for url in (self.url("employee_tickets_request_paid", allowance.pk),):
            r = self.client.get(url)
            self.assertEqual({n for s in r.context["layout"] for row in s["rows"] for n in row},
                             set(r.context["form"].fields), url)
        tickets.cancel(allowance, self.hr, reason="test")
        r = self.client.get(self.url("employee_tickets_request_add", self.emp.pk))
        self.assertEqual({n for s in r.context["layout"] for row in s["rows"] for n in row},
                         set(r.context["form"].fields))
