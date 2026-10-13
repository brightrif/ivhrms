from django.urls import reverse

from apps.core.models import ApprovalRequest
from apps.dependants import visa_services as visa
from apps.dependants.models import Dependant
from apps.dependants.testing import VisaCase
from apps.dependants.visa import FamilyVisaRequest, VisaPayment

Kind = FamilyVisaRequest.Kind


class VisaPageCase(VisaCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.hr)

    def url(self, name, *args):
        return reverse(f"web:{name}", args=args)


class AccessTests(VisaPageCase):
    def test_who_can_look_ask_and_see_costs(self):
        add = self.url("employee_family_request_add", self.kid.pk)
        requests_, costs = self.url("employee_family_requests"), self.url("employee_family_costs")
        self.client.logout()
        self.assertEqual(self.client.get(requests_).status_code, 302)                  # sign in first
        expected = {self.hr: (200, 200, 200), self.finance: (200, 403, 200), self.boss: (200, 403, 200),
                    self.nobody: (403, 403, 403), self.other_hr: (200, 404, 200)}
        for user, (look, ask, cost) in expected.items():
            self.client.force_login(user)
            self.assertEqual(self.client.get(requests_).status_code, look, user.username)
            self.assertEqual(self.client.get(add).status_code, ask, user.username)
            self.assertEqual(self.client.get(costs).status_code, cost, user.username)

    def test_another_companys_requests_are_invisible(self):
        req = self.ask()
        self.client.force_login(self.other_hr)
        self.assertEqual(self.client.get(self.url("employee_family_request", req.pk)).status_code, 404)
        self.assertNotContains(self.client.get(self.url("employee_family_requests")), "Yusuf")

    def test_finance_records_costs_but_does_not_process_requests(self):
        req = self.approve(self.ask())
        self.client.force_login(self.finance)
        self.assertEqual(self.client.get(self.url("employee_family_request_payment", req.pk)).status_code, 200)
        for name in ("employee_family_request_submitted", "employee_family_request_complete"):
            self.assertEqual(self.client.get(self.url(name, req.pk)).status_code, 403, name)


class AskAndApproveTests(VisaPageCase):
    def test_hr_asks_the_approvers_see_it_in_their_inbox_and_decide_there(self):
        add = self.url("employee_family_request_add", self.kid.pk)
        r = self.client.post(add, {"kind": "new", "reason": "Family is joining"})
        req = FamilyVisaRequest.objects.get()
        self.assertRedirects(r, self.url("employee_family_request", req.pk))
        page = self.client.get(self.url("employee_family_request", req.pk))
        self.assertContains(page, "Reporting manager")
        self.assertContains(page, "Waiting for this approval")

        approval = ApprovalRequest.objects.get(object_id=str(req.pk))
        decide = self.url("approval_decide", approval.pk)
        self.client.force_login(self.boss)
        inbox = self.client.get(self.url("approvals"))
        self.assertContains(inbox, "Yusuf")
        self.assertContains(inbox, "New visa: Yusuf")                                  # the approver can read what it is
        self.assertEqual(self.client.post(decide, {"decision": "approve"}).status_code, 200)
        self.client.force_login(self.hr_b)
        self.assertEqual(self.client.post(decide, {"decision": "approve"}).status_code, 200)
        req.refresh_from_db()
        self.assertEqual(req.status, "approved")
        self.client.force_login(self.hr)
        self.assertContains(self.client.get(self.url("employee_family_request", req.pk)), "Mark as submitted")

    def test_only_the_right_kinds_are_offered_and_one_request_at_a_time(self):
        add = self.url("employee_family_request_add", self.kid.pk)
        form = self.client.get(add).context["form"]
        self.assertEqual([k for k, _ in form.fields["kind"].choices], ["new"])
        self.client.post(add, {"kind": "new", "reason": ""})
        r = self.client.get(add)
        self.assertRedirects(r, self.url("employee_family_detail", self.kid.pk))      # one is already in progress

    def test_a_cancellation_needs_a_reason_and_the_message_is_shown(self):
        self.make_visa()
        r = self.client.post(self.url("employee_family_request_add", self.kid.pk), {"kind": "cancellation", "reason": ""})
        self.assertContains(r, "Give the reason")
        self.assertFalse(FamilyVisaRequest.objects.exists())

    def test_no_reporting_manager_is_explained_on_the_form(self):
        noor = self.person("Noor", employee=self.emp2, relationship="spouse")
        r = self.client.post(self.url("employee_family_request_add", noor.pk), {"kind": "new", "reason": ""})
        self.assertContains(r, "reporting manager")

    def test_only_the_person_who_asked_can_withdraw(self):
        req = self.ask()
        url = self.url("employee_family_request_withdraw", req.pk)
        self.assertEqual(self.client.get(url).status_code, 405)
        self.client.force_login(self.hr_b)
        self.client.post(url)
        req.refresh_from_db()
        self.assertEqual(req.status, "pending")
        self.client.force_login(self.hr)
        self.client.post(url)
        req.refresh_from_db()
        self.assertEqual(req.status, "cancelled")


class ProcessingPageTests(VisaPageCase):
    def test_submit_then_record_the_visa_and_it_shows_on_the_family_page(self):
        req = self.approve(self.ask())
        self.assertRedirects(self.client.post(self.url("employee_family_request_submitted", req.pk),
                                              {"submitted_on": self.iso(0), "reference": "A1"}),
                             self.url("employee_family_request", req.pk))
        r = self.client.post(self.url("employee_family_request_complete", req.pk),
                             {"number": "FV-5", "issue_date": self.iso(0), "expiry_date": self.iso(730),
                              "completed_on": self.iso(0)})
        self.assertRedirects(r, self.url("employee_family_request", req.pk))
        req.refresh_from_db()
        self.assertEqual(req.status, "completed")
        self.assertContains(self.client.get(self.url("employee_family_detail", self.kid.pk)), "FV-5")

    def test_problems_are_shown_on_the_form(self):
        req = self.ask()                                                              # still waiting for approval
        url = self.url("employee_family_request_complete", req.pk)
        r = self.client.post(url, {"number": "FV-5", "expiry_date": self.iso(730), "completed_on": self.iso(0)})
        self.assertContains(r, "Only an approved request can be completed")
        req = self.approve(req)
        r = self.client.post(url, {"number": "FV-5", "issue_date": self.iso(0), "expiry_date": self.iso(-5),
                                   "completed_on": self.iso(0)})
        self.assertContains(r, "cannot be before")
        self.assertEqual(self.client.post(url, {"completed_on": self.iso(0)}).status_code, 200)   # number and expiry needed

    def test_costs_are_recorded_and_reported(self):
        req = self.approve(self.ask())
        url = self.url("employee_family_request_payment", req.pk)
        r = self.client.post(url, {"description": "Government fee", "paid_on": self.iso(0), "government_fee": "40.000",
                                   "charged_to_employee": "on"})
        self.assertRedirects(r, self.url("employee_family_request", req.pk))
        self.assertContains(self.client.get(self.url("employee_family_request", req.pk)), "40.000")
        costs = self.client.get(self.url("employee_family_costs"))
        self.assertContains(costs, "40.000")
        self.assertContains(costs, "New visa")
        r = self.client.post(url, {"paid_on": self.iso(0)})
        self.assertContains(r, "greater than zero")
        self.assertEqual(VisaPayment.objects.count(), 1)

    def test_costs_cannot_be_recorded_before_approval(self):
        req = self.ask()
        r = self.client.post(self.url("employee_family_request_payment", req.pk),
                             {"paid_on": self.iso(0), "government_fee": "5"})
        self.assertContains(r, "once the request is approved")


class ListAndFamilyPageTests(VisaPageCase):
    def test_the_list_shows_what_is_in_progress_and_filters_the_rest(self):
        layla = self.person("Layla")
        done = self.ask(dependant=layla)
        visa.cancel_request(done, self.hr)
        self.ask()
        url = self.url("employee_family_requests")
        r = self.client.get(url)
        self.assertContains(r, "Yusuf")
        self.assertNotContains(r, "Layla")
        r = self.client.get(url, {"status": "cancelled"})
        self.assertContains(r, "Layla")
        self.assertNotContains(r, "Yusuf")
        self.assertContains(self.client.get(url, {"status": "all", "kind": "new"}), "Layla")
        self.assertNotContains(self.client.get(url, {"kind": "renewal"}), "Yusuf")
        self.assertContains(self.client.get(url, {"q": "Ali"}), "Yusuf")              # the sponsor's name finds it

    def test_the_family_page_offers_a_request_until_one_is_open(self):
        detail = self.url("employee_family_detail", self.kid.pk)
        self.assertContains(self.client.get(detail), "New request")
        self.ask()
        r = self.client.get(detail)
        self.assertNotContains(r, "New request")
        self.assertContains(r, "Waiting for approval")
        self.client.force_login(self.nobody)
        self.assertEqual(self.client.get(detail).status_code, 403)

    def test_the_family_page_hides_the_requests_card_from_people_who_may_not_see_it(self):
        from django.contrib.auth.models import Permission
        self.nobody.user_permissions.add(*Permission.objects.filter(
            content_type__app_label="dependants", codename="view_dependant"))
        from apps.organization.models import CompanyAccess
        CompanyAccess.objects.create(user=self.nobody, company=self.co)
        self.client.force_login(self.nobody)
        r = self.client.get(self.url("employee_family_detail", self.kid.pk))
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, "Visa requests")


class LayoutTests(VisaPageCase):
    def test_every_form_places_every_field_it_has(self):
        new = self.approve(self.ask())
        other = self.person("Layla")
        self.make_visa(other)
        cancel = self.approve(self.ask(Kind.CANCELLATION, dependant=other, reason="Returning home"))
        pages = [self.url("employee_family_request_add", self.person("Sami").pk),
                 self.url("employee_family_request_submitted", new.pk),
                 self.url("employee_family_request_complete", new.pk),
                 self.url("employee_family_request_complete", cancel.pk),
                 self.url("employee_family_request_payment", new.pk)]
        for url in pages:
            r = self.client.get(url)
            self.assertEqual(r.status_code, 200, url)
            placed = {n for section in r.context["layout"] for row in section["rows"] for n in row}
            self.assertEqual(placed, set(r.context["form"].fields), url)
