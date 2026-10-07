from datetime import timedelta

from django.core import mail

from apps.compliance.models import Document, DocumentType
from apps.core import approvals
from apps.core.models import ApprovalFlow, ApprovalRequest
from apps.employees.models import Employee

from . import assignments, fuel, handovers, reports, services
from .custody import CustodyRequest, LeaverAlertLog
from .models import Vehicle
from .test_upkeep import UpkeepCase
from .usage import VehicleAssignment

Decision, Status = CustodyRequest.Decision, CustodyRequest.Status


class CustodyCase(UpkeepCase):
    def licensed(self, name):
        person = self.person(name)
        dtype = DocumentType.objects.get_or_create(
            code="driving-licence", defaults=dict(name="Driving Licence", applies_to="employee"))[0]
        Document.objects.create(employee=person, document_type=dtype, expiry_date=self.today + timedelta(days=300))
        return person

    def set_status(self, person, status):
        Employee.objects.filter(pk=person.pk).update(status=status)
        person.refresh_from_db()

    def to(self):
        return sorted(m.to[0] for m in mail.outbox)


class HandOverTests(CustodyCase):
    def setUp(self):
        super().setUp()
        self.v, self.ali, self.bob = self.vehicle(), self.licensed("Ali"), self.licensed("Bob")
        assignments.assign_vehicle(self.v, self.ali, self.ago(10), 1000)

    def test_one_step_hand_over_and_hand_back(self):
        handovers.hand_over(self.v, self.bob, self.today, 1200, notes="Ali is on a business trip")
        self.assertEqual(assignments.current_assignment(self.v).employee, self.bob)
        first = self.v.assignments.get(employee=self.ali)
        self.assertEqual((first.assigned_to, first.end_odometer), (self.today, 1200))
        handovers.hand_over(self.v, self.ali, self.today, 1200)                           # Ali is back: hand it back
        self.assertEqual(assignments.current_assignment(self.v).employee, self.ali)
        self.assertEqual(self.v.assignments.count(), 3)
        self.v.refresh_from_db()
        self.assertEqual(self.v.odometer, 1200)

    def test_nothing_changes_if_the_new_driver_cannot_have_it(self):
        carl = self.person("Carl")                                                      # no driving licence
        with self.assertRaises(assignments.LicenceProblem):
            handovers.hand_over(self.v, carl, self.today, 1200)
        self.assertEqual(assignments.current_assignment(self.v).employee, self.ali)       # still with Ali
        self.assertEqual(self.v.assignments.count(), 1)
        handovers.hand_over(self.v, carl, self.today, 1200, licence_override=True)
        self.assertEqual(assignments.current_assignment(self.v).employee, carl)

    def test_hand_over_rules(self):
        with self.assertRaises(services.VehicleError):                                    # already with Ali
            handovers.hand_over(self.v, self.ali, self.today, 1200)
        with self.assertRaises(services.VehicleError):                                    # the odometer cannot go down
            handovers.hand_over(self.v, self.bob, self.today, 900)
        with self.assertRaises(services.VehicleError):                                    # nor be handed over in the future
            handovers.hand_over(self.v, self.bob, self.today + timedelta(days=1), 1200)
        self.assertEqual(assignments.current_assignment(self.v).employee, self.ali)

    def test_a_vehicle_without_a_driver_is_simply_assigned(self):
        free = self.vehicle("2")
        handovers.hand_over(free, self.bob, self.today, 50)
        self.assertEqual(assignments.current_assignment(free).employee, self.bob)


class WorkshopTests(CustodyCase):
    def test_in_and_out(self):
        v = self.vehicle()
        self.assertEqual(handovers.send_to_workshop(v).status, Vehicle.Status.WORKSHOP)
        with self.assertRaises(services.VehicleError):
            handovers.send_to_workshop(v)                                               # already there
        fuel_fill = fuel.add_fill(v, filled_on=self.today, litres=10, cost=5, km=10)    # entries still work
        self.assertTrue(fuel_fill.pk)
        self.assertEqual(handovers.back_from_workshop(v).status, Vehicle.Status.ACTIVE)
        with self.assertRaises(services.VehicleError):
            handovers.back_from_workshop(v)

    def test_a_sold_vehicle_cannot_go_to_the_workshop(self):
        v = self.vehicle()
        services.mark_sold(v, self.today)
        with self.assertRaises(services.VehicleError):
            handovers.send_to_workshop(v)


class CustodyDecisionTests(CustodyCase):
    def setUp(self):
        super().setUp()
        self.v, self.ali, self.bob = self.vehicle(), self.person("Ali"), self.person("Bob")
        self.held = assignments.assign_vehicle(self.v, self.ali, self.ago(10), 1000, licence_override=True)

    def ask(self, decision=Decision.HANDOVER, **kw):
        kw.setdefault("new_driver", self.bob if decision == Decision.HANDOVER else None)
        return handovers.request_custody_decision(self.held, decision, requested_by=self.hr, **kw)

    def approval_of(self, request):
        return ApprovalRequest.objects.get(object_id=str(request.pk), flow__code=handovers.FLOW_CODE)

    def test_a_default_flow_exists_after_migrate(self):
        flow = ApprovalFlow.objects.get(code=handovers.FLOW_CODE, company__isnull=True)
        step = flow.steps.get()
        self.assertEqual((step.order, step.approver_type, step.group.name), (1, "group", "Management"))

    def test_only_for_someone_who_is_leaving(self):
        with self.assertRaises(services.VehicleError):                                    # still active
            self.ask()
        self.set_status(self.ali, Employee.Status.ON_NOTICE)
        self.assertEqual(self.ask().status, Status.PENDING)

    def test_each_decision_has_its_rules(self):
        self.set_status(self.ali, Employee.Status.ON_NOTICE)
        gone = self.person("Gone")
        self.set_status(gone, Employee.Status.SEPARATED)
        elsewhere = self.person("Zed")
        Employee.objects.filter(pk=elsewhere.pk).update(company=self.other_co)
        elsewhere.refresh_from_db()
        for kwargs in (dict(new_driver=None),                                             # nobody chosen
                       dict(new_driver=self.ali),                                         # the same person
                       dict(new_driver=gone),                                             # someone who has left
                       dict(new_driver=elsewhere)):                                       # another company
            with self.assertRaises(services.VehicleError, msg=kwargs):
                self.ask(Decision.HANDOVER, **kwargs)
        with self.assertRaises(services.VehicleError):
            self.ask(Decision.KEEP, reason="  ")                                          # a reason is needed
        with self.assertRaises(services.VehicleError):
            self.ask("nonsense")
        self.assertFalse(CustodyRequest.objects.exists())
        self.assertEqual(self.ask(Decision.KEEP, reason="Director: the car stays at his house").decision, Decision.KEEP)

    def test_one_request_at_a_time_and_none_for_a_returned_or_sold_vehicle(self):
        self.set_status(self.ali, Employee.Status.ON_NOTICE)
        self.ask()
        with self.assertRaises(services.VehicleError):
            self.ask(Decision.RETURN)
        assignments.return_vehicle(self.held, self.today, 1100)
        with self.assertRaises(services.VehicleError):
            self.ask(Decision.RETURN)                                                     # it is no longer with Ali

    def test_management_approves_and_nothing_moves_by_itself(self):
        self.set_status(self.ali, Employee.Status.ON_NOTICE)
        request = self.ask(planned_on=self.today + timedelta(days=20))
        approvals.decide(self.approval_of(request).pk, self.boss, "approve")
        request.refresh_from_db()
        self.assertEqual(request.status, Status.APPROVED)
        self.assertEqual(assignments.current_assignment(self.v).employee, self.ali)       # HR carries it out
        self.assertEqual(handovers.current_decision(self.held), request)
        handovers.hand_over(self.v, self.bob, self.today, 1200, licence_override=True)    # ...with the real date and odometer
        self.assertEqual(assignments.current_assignment(self.v).employee, self.bob)

    def test_a_rejection_needs_a_comment_and_is_recorded(self):
        self.set_status(self.ali, Employee.Status.ON_NOTICE)
        request = self.ask()
        with self.assertRaises(approvals.ApprovalError):
            approvals.decide(self.approval_of(request).pk, self.boss, "reject")
        approvals.decide(self.approval_of(request).pk, self.boss, "reject", comment="Bob already has a vehicle")
        request.refresh_from_db()
        self.assertEqual(request.status, Status.REJECTED)
        self.assertEqual(self.ask(Decision.RETURN).status, Status.PENDING)                # HR can decide again

    def test_only_management_can_decide_and_nobody_approves_their_own_request(self):
        self.set_status(self.ali, Employee.Status.ON_NOTICE)
        request = self.ask()
        pk = self.approval_of(request).pk
        for user in (self.hr, self.finance, self.other_hr, self.nobody):
            with self.assertRaises(approvals.ApprovalError, msg=user.username):
                approvals.decide(pk, user, "approve")
        request.refresh_from_db()
        self.assertEqual(request.status, Status.PENDING)

    def test_the_requester_can_cancel_but_nobody_else(self):
        self.set_status(self.ali, Employee.Status.ON_NOTICE)
        request = self.ask()
        with self.assertRaises(services.VehicleError):
            handovers.cancel_request(request, self.boss)
        self.assertEqual(handovers.cancel_request(request, self.hr).status, Status.CANCELLED)
        self.assertIsNone(handovers.current_decision(self.held))                          # cancelled ones are ignored
        with self.assertRaises(services.VehicleError):
            handovers.cancel_request(request, self.hr)

    def test_with_nobody_to_approve_nothing_is_created(self):
        v = Vehicle.objects.create(company=self.other_co, plate_number="X1", make="Kia")
        carl = Employee.objects.create(company=self.other_co, employee_no="C-1", first_name="Carl",
                                       joining_date=self.today - timedelta(days=500), status="on_notice")
        held = assignments.assign_vehicle(v, carl, self.ago(5), 10, licence_override=True)
        with self.assertRaises(services.VehicleError) as ctx:
            handovers.request_custody_decision(held, Decision.RETURN, requested_by=self.other_hr)
        self.assertIn("No approver", str(ctx.exception))
        self.assertEqual((CustodyRequest.objects.count(), ApprovalRequest.objects.count()), (0, 0))


class LeaverNoticeTests(CustodyCase):
    def setUp(self):
        super().setUp()
        self.v, self.ali = self.vehicle(), self.person("Ali")
        self.held = assignments.assign_vehicle(self.v, self.ali, self.ago(10), 1000, licence_override=True)

    def test_nothing_while_the_holder_is_active(self):
        self.assertEqual(handovers.run_leaver_scan()["checked"], 0)

    def test_hr_is_told_once_on_notice_and_once_when_they_have_left(self):
        self.set_status(self.ali, Employee.Status.ON_NOTICE)
        self.assertEqual(handovers.run_leaver_scan()["alerts"], 1)
        self.assertEqual(self.to(), ["hr@example.com"])
        self.assertIn("is on notice", mail.outbox[0].subject)
        self.assertEqual(handovers.run_leaver_scan()["alerts"], 0)
        self.set_status(self.ali, Employee.Status.SEPARATED)
        self.assertEqual(handovers.run_leaver_scan()["alerts"], 1)
        self.assertIn("has left", mail.outbox[-1].subject)
        self.assertEqual(handovers.run_leaver_scan()["alerts"], 0)                        # nothing is blocked, just told

    def test_a_decision_in_progress_quietens_the_notice_but_not_the_leaving(self):
        self.set_status(self.ali, Employee.Status.ON_NOTICE)
        request = handovers.request_custody_decision(self.held, Decision.KEEP, requested_by=self.hr,
                                                     reason="Director: the car stays at his house")
        self.assertEqual(handovers.run_leaver_scan()["alerts"], 0)                        # already being decided
        approvals.decide(ApprovalRequest.objects.get(object_id=str(request.pk)).pk, self.boss, "approve")
        self.assertEqual(handovers.run_leaver_scan()["alerts"], 0)                        # approved: still quiet
        self.set_status(self.ali, Employee.Status.SEPARATED)
        self.assertEqual(handovers.run_leaver_scan()["alerts"], 1)                        # but HR is told they left
        self.assertIn("approved to stay with them", mail.outbox[-1].body)
        self.assertIn("the car stays at his house", mail.outbox[-1].body)

    def test_sold_vehicles_and_other_companies_hr_are_left_out(self):
        self.set_status(self.ali, Employee.Status.SEPARATED)
        handovers.run_leaver_scan()
        self.assertNotIn("hr2@example.com", self.to())
        services.mark_sold(self.v, self.today)
        VehicleAssignment.objects.filter(pk=self.held.pk).update(assigned_to=self.today, end_odometer=1000)
        LeaverAlertLog.objects.all().delete()
        self.assertEqual(handovers.run_leaver_scan()["checked"], 0)

    def test_the_notice_is_logged_even_when_hr_has_no_email(self):
        self.hr.email = ""
        self.hr.save()
        self.set_status(self.ali, Employee.Status.ON_NOTICE)
        self.assertEqual(handovers.run_leaver_scan()["alerts"], 1)
        self.assertEqual(LeaverAlertLog.objects.get().recipients, 0)

    def test_the_dashboard_lists_people_on_notice_with_their_decision(self):
        self.set_status(self.ali, Employee.Status.ON_NOTICE)
        row = reports.dashboard(self.hr)["leavers"][0]
        self.assertEqual((row.vehicle, row.state, row.decision), (self.v, "notice", None))
        request = handovers.request_custody_decision(self.held, Decision.RETURN, requested_by=self.hr)
        self.assertEqual(reports.dashboard(self.hr)["leavers"][0].decision, request)
        self.set_status(self.ali, Employee.Status.SEPARATED)
        self.assertEqual(reports.dashboard(self.hr)["leavers"][0].state, "left")
