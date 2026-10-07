from datetime import date, timedelta
from decimal import Decimal as D

from django.urls import reverse

from apps.compliance.models import Document, DocumentType
from apps.compliance.testing import ComplianceCase
from apps.core import approvals
from apps.core.models import ApprovalRequest
from apps.employees.models import Employee
from apps.vehicles import assignments, fines, handovers
from apps.vehicles.custody import CustodyRequest
from apps.vehicles.incidents import Fine
from apps.vehicles.models import Vehicle


class CustodyPageCase(ComplianceCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.hr)
        self.v = Vehicle.objects.create(company=self.co, plate_number="123456", make="Toyota", model="Hilux", year=2022)
        self.ali, self.bob = self.person("Ali"), self.person("Bob", licensed=True)
        self.held = assignments.assign_vehicle(self.v, self.ali, self.today - timedelta(days=10), 1000,
                                               licence_override=True)

    def url(self, name, *args):
        return reverse(f"web:{name}", args=args)

    def iso(self, days=0):
        return (self.today + timedelta(days=days)).isoformat()

    def person(self, name, licensed=False):
        emp = Employee.objects.create(company=self.co, employee_no=f"T-{name}", first_name=name,
                                      joining_date=date(2022, 1, 1))
        if licensed:
            dtype = DocumentType.objects.get_or_create(
                code="driving-licence", defaults=dict(name="Driving Licence", applies_to="employee"))[0]
            Document.objects.create(employee=emp, document_type=dtype, expiry_date=self.today + timedelta(days=300))
        return emp

    def set_status(self, emp, status):
        Employee.objects.filter(pk=emp.pk).update(status=status)
        emp.refresh_from_db()

    def approval_of(self, request):
        return ApprovalRequest.objects.get(object_id=str(request.pk), flow__code=handovers.FLOW_CODE)


class WorkshopAndHandOverPageTests(CustodyPageCase):
    def test_workshop_buttons(self):
        send, back = self.url("vehicle_workshop_send", self.v.pk), self.url("vehicle_workshop_back", self.v.pk)
        self.assertEqual(self.client.get(send).status_code, 405)                            # a button, not an address
        self.assertRedirects(self.client.post(send), self.url("vehicle_detail", self.v.pk), fetch_redirect_response=False)
        self.v.refresh_from_db()
        self.assertEqual(self.v.status, "workshop")
        self.client.post(send)                                                              # twice: a message, not a crash
        self.client.post(back)
        self.v.refresh_from_db()
        self.assertEqual(self.v.status, "active")
        self.client.force_login(self.finance)
        self.assertEqual(self.client.post(send).status_code, 403)                           # only people who may edit vehicles

    def test_a_one_step_hand_over_and_the_hand_back_shortcut(self):
        page = self.url("vehicle_handover", self.v.pk)
        r = self.client.get(page)
        self.assertEqual(r.status_code, 200)
        self.assertNotIn(self.ali, r.context["form"].fields["employee"].queryset)            # not to the person who has it
        data = {"employee": self.bob.pk, "assigned_from": self.iso(0), "odometer": "1200", "notes": "Ali is away"}
        self.assertRedirects(self.client.post(page, data), self.url("vehicle_detail", self.v.pk),
                             fetch_redirect_response=False)
        self.assertEqual(assignments.current_assignment(self.v).employee, self.bob)
        self.assertEqual(self.v.assignments.count(), 2)
        back = self.client.get(page, {"employee": self.ali.pk})                            # "hand back to Ali"
        self.assertEqual(str(back.context["form"].initial["employee"]), str(self.ali.pk))

    def test_a_driver_without_a_licence_is_a_form_error_and_nothing_changes(self):
        carl = self.person("Carl")
        r = self.client.post(self.url("vehicle_handover", self.v.pk),
                             {"employee": carl.pk, "assigned_from": self.iso(0), "odometer": "1200"})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "no driving licence")
        self.assertEqual(assignments.current_assignment(self.v).employee, self.ali)
        self.assertEqual(self.v.assignments.count(), 1)

    def test_a_sold_vehicle_cannot_be_handed_over(self):
        Vehicle.objects.filter(pk=self.v.pk).update(status="sold", sold_on=self.today)
        self.assertEqual(self.client.get(self.url("vehicle_handover", self.v.pk)).status_code, 302)


class CustodyDecisionPageTests(CustodyPageCase):
    def post_decision(self, **over):
        data = {"decision": "handover", "new_driver": self.bob.pk, "planned_on": self.iso(20), "reason": ""}
        data.update(over)
        return self.client.post(self.url("vehicle_custody_request", self.v.pk), data)

    def test_the_request_goes_through_management_approval(self):
        self.set_status(self.ali, "on_notice")
        page = self.url("vehicle_custody_request", self.v.pk)
        self.assertEqual(self.client.get(page).status_code, 200)
        self.assertRedirects(self.post_decision(), self.url("vehicle_detail", self.v.pk), fetch_redirect_response=False)
        request = CustodyRequest.objects.get()
        self.assertEqual((request.status, request.new_driver, request.company), ("pending", self.bob, self.co))
        approvals.decide(self.approval_of(request).pk, self.boss, "approve")
        request.refresh_from_db()
        self.assertEqual(request.status, "approved")

    def test_a_bad_request_is_a_form_error_not_a_crash(self):
        r = self.post_decision()                                                            # Ali is still active
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "still an active employee")
        self.set_status(self.ali, "on_notice")
        for over in ({"decision": "keep", "new_driver": "", "reason": ""}, {"new_driver": ""}, {"decision": "nonsense"}):
            self.assertEqual(self.post_decision(**over).status_code, 200, over)
        self.assertFalse(CustodyRequest.objects.exists())

    def test_the_requester_can_cancel_it(self):
        self.set_status(self.ali, "on_notice")
        self.post_decision()
        request = CustodyRequest.objects.get()
        cancel = self.url("vehicle_custody_cancel", request.pk)
        self.assertEqual(self.client.get(cancel).status_code, 405)
        self.client.post(cancel)
        request.refresh_from_db()
        self.assertEqual(request.status, "cancelled")

    def test_a_vehicle_nobody_holds_has_nothing_to_decide(self):
        assignments.return_vehicle(self.held, self.today, 1100)
        self.assertEqual(self.client.get(self.url("vehicle_custody_request", self.v.pk)).status_code, 302)

    def test_who_may_ask(self):
        page = self.url("vehicle_custody_request", self.v.pk)
        for user, expected in ((self.hr, 200), (self.finance, 403), (self.boss, 403), (self.nobody, 403),
                               (self.other_hr, 404)):
            self.client.force_login(user)
            self.assertEqual(self.client.get(page).status_code, expected, user.username)

    def test_the_decision_form_places_every_field_it_has(self):
        r = self.client.get(self.url("vehicle_custody_request", self.v.pk))
        placed = {n for section in r.context["layout"] for row in section["rows"] for n in row}
        self.assertEqual(placed, set(r.context["form"].fields))


class RecoverPageTests(CustodyPageCase):
    def setUp(self):
        super().setUp()
        self.fine = fines.save_fine(Fine(vehicle=self.v, fined_on=self.today - timedelta(days=2), offence="Speeding",
                                         amount=D("20"), reference="T-1", charged_to_employee=True))

    def test_the_list_and_marking_a_fine_recovered(self):
        page = self.url("vehicle_fines_recover")
        r = self.client.get(page)
        self.assertContains(r, "Ali")
        self.assertContains(r, "20.000")
        recover = self.url("vehicle_fine_recover", self.fine.pk)
        self.assertEqual(self.client.get(recover).status_code, 200)
        self.assertRedirects(self.client.post(recover, {"recovered_on": self.iso(0), "note": "April salary"}), page)
        self.fine.refresh_from_db()
        self.assertEqual((self.fine.recovered_on, self.fine.recovered_note), (self.today, "April salary"))
        r = self.client.get(page)
        self.assertContains(r, "Nothing is waiting to be recovered")
        self.assertContains(r, "April salary")
        undo = self.url("vehicle_fine_unrecover", self.fine.pk)
        self.assertEqual(self.client.post(undo, {"reason": ""}).status_code, 200)
        self.assertRedirects(self.client.post(undo, {"reason": "wrong fine"}), page)
        self.fine.refresh_from_db()
        self.assertIsNone(self.fine.recovered_on)

    def test_a_bad_date_is_a_form_error(self):
        r = self.client.post(self.url("vehicle_fine_recover", self.fine.pk),
                             {"recovered_on": self.iso(3), "note": ""})
        self.assertEqual(r.status_code, 200)
        self.fine.refresh_from_db()
        self.assertIsNone(self.fine.recovered_on)

    def test_who_may_see_and_who_may_mark(self):
        page, recover = self.url("vehicle_fines_recover"), self.url("vehicle_fine_recover", self.fine.pk)
        for user, see, mark in ((self.hr, 200, 200), (self.finance, 200, 200), (self.boss, 200, 403),
                                (self.nobody, 403, 403), (self.other_hr, 200, 404)):
            self.client.force_login(user)
            self.assertEqual(self.client.get(page).status_code, see, user.username)
            self.assertEqual(self.client.get(recover).status_code, mark, user.username)
        self.client.force_login(self.other_hr)
        self.assertNotContains(self.client.get(page), "Ali")                                # another company's fines

    def test_the_forms_place_every_field_they_have(self):
        self.client.post(self.url("vehicle_fine_recover", self.fine.pk), {"recovered_on": self.iso(0), "note": ""})
        for name in ("vehicle_fine_recover", "vehicle_fine_unrecover"):
            r = self.client.get(self.url(name, self.fine.pk))
            placed = {n for section in r.context["layout"] for row in section["rows"] for n in row}
            self.assertEqual(placed, set(r.context["form"].fields), name)


class RealApprovalAndPagesTests(CustodyPageCase):
    """Uses your real approvals inbox, vehicle page and dashboard, which the other tests do not touch."""

    def test_management_decides_in_the_normal_approvals_inbox(self):
        self.set_status(self.ali, "on_notice")
        self.client.post(self.url("vehicle_custody_request", self.v.pk),
                         {"decision": "keep", "new_driver": "", "planned_on": "", "reason": "Director: car stays at home"})
        request = CustodyRequest.objects.get()
        approval = self.approval_of(request)
        self.client.force_login(self.boss)
        self.assertContains(self.client.get(self.url("approvals")), "123456")               # it is in the inbox
        r = self.client.post(self.url("approval_decide", approval.pk), {"decision": "approve"})
        self.assertEqual(r.status_code, 200)
        request.refresh_from_db()
        self.assertEqual(request.status, "approved")

    def test_the_vehicle_page_shows_what_is_going_on(self):
        page = self.url("vehicle_detail", self.v.pk)
        self.assertNotContains(self.client.get(page), "still holds this vehicle")           # an active holder: nothing
        self.set_status(self.ali, "on_notice")
        r = self.client.get(page)
        self.assertContains(r, "is on notice and still holds this vehicle")
        self.assertContains(r, "Decide what happens to it")
        self.assertContains(r, "Send to workshop")
        self.client.force_login(self.finance)
        self.assertContains(self.client.get(page), "HR needs to decide")
        self.client.force_login(self.hr)
        self.client.post(self.url("vehicle_custody_request", self.v.pk),
                         {"decision": "handover", "new_driver": self.bob.pk, "planned_on": "", "reason": ""})
        r = self.client.get(page)
        self.assertContains(r, "Waiting for Management")
        approvals.decide(self.approval_of(CustodyRequest.objects.get()).pk, self.boss, "approve")
        r = self.client.get(page)
        self.assertContains(r, "Approved: hand it over to Bob")
        self.assertContains(r, "Carry out the hand-over")
        self.assertContains(self.client.get(self.url("vehicle_dashboard")), "on notice or who have left")

    def test_hand_back_to_the_previous_driver(self):
        handovers.hand_over(self.v, self.bob, self.today, 1200)
        r = self.client.get(self.url("vehicle_detail", self.v.pk))
        self.assertContains(r, "Hand back to Ali")
        self.assertContains(r, "?employee=" + str(self.ali.pk))

    def test_the_workshop_status_shows_on_the_vehicle_page(self):
        self.client.post(self.url("vehicle_workshop_send", self.v.pk))
        r = self.client.get(self.url("vehicle_detail", self.v.pk))
        self.assertContains(r, "In workshop")
        self.assertContains(r, "Back from workshop")
