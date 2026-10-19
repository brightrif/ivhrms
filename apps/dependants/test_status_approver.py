from apps.accounts.models import User
from apps.core import approvals
from apps.core.models import ApprovalFlow, ApprovalRequest, ApprovalStep
from apps.employees.models import Employee
from apps.organization.models import Company

from . import eligibility
from . import status_approver as chosen
from . import status_services as svc
from .status import FamilyStatusRequest
from .testing import VisaCase

Status = FamilyStatusRequest.Status


class ApproverCase(VisaCase):
    """The shared set-up allows its sponsors and gives emp a reporting manager (boss) and emp2 none."""

    def setUp(self):
        super().setUp()
        for sponsor in (self.emp, self.emp2):
            eligibility.set_exception(sponsor, "default", "")
        self.investor = User.objects.create_user("investor", password="pw", first_name="Ibrahim")
        self.login = User.objects.create_user("ali")
        Employee.objects.filter(pk=self.emp.pk).update(user=self.login)
        self.emp.refresh_from_db()

    def approval(self, req):
        return ApprovalRequest.objects.get(object_id=str(req.pk), flow__code=svc.FLOW_CODE)


class DecidingTests(ApproverCase):
    def test_without_a_named_approver_the_reporting_manager_decides(self):
        self.assertIsNone(chosen.approver_for(self.co))
        req = svc.ask(self.emp, requested_by=self.hr)
        self.assertTrue(approvals.can_act(self.approval(req), self.boss))
        self.assertFalse(approvals.can_act(self.approval(req), self.investor))

    def test_a_named_person_decides_every_request_even_for_someone_with_no_reporting_manager(self):
        chosen.set_approver(self.hr, self.co, self.investor)
        req = svc.ask(self.emp2, requested_by=self.hr)                       # emp2 has no reporting manager
        self.assertEqual(req.status, Status.PENDING)
        self.assertIn(self.approval(req), approvals.pending_for(self.investor))
        approvals.decide(self.approval(req).pk, self.investor, "approve", comment="Fine")
        req.refresh_from_db()
        self.assertEqual(req.status, Status.APPROVED)
        self.assertTrue(eligibility.sponsor_status(Employee.objects.get(pk=self.emp2.pk)).allowed)

    def test_the_reporting_manager_no_longer_decides_once_one_person_is_named(self):
        chosen.set_approver(self.hr, self.co, self.investor)
        req = svc.ask(self.emp, requested_by=self.hr)                        # emp does have a manager: boss
        self.assertFalse(approvals.can_act(self.approval(req), self.boss))
        self.assertTrue(approvals.can_act(self.approval(req), self.investor))

    def test_the_employees_own_request_goes_through_hr_and_then_to_the_named_person(self):
        chosen.set_approver(self.hr, self.co, self.investor)
        req = svc.ask(self.emp, requested_by=self.login, reason="Family joining")
        self.assertEqual(req.status, Status.WAITING_HR)
        svc.forward(req, self.hr)
        self.assertTrue(approvals.can_act(self.approval(req), self.investor))

    def test_requests_waiting_stay_with_their_approver_when_a_person_is_named_later(self):
        req = svc.ask(self.emp, requested_by=self.hr)                        # waiting for boss
        chosen.set_approver(self.hr, self.co, self.investor)
        self.assertTrue(approvals.can_act(self.approval(req), self.boss))
        self.assertFalse(approvals.can_act(self.approval(req), self.investor))

    def test_replacing_the_named_person_moves_the_requests_waiting_for_them(self):
        chosen.set_approver(self.hr, self.co, self.investor)
        req = svc.ask(self.emp, requested_by=self.hr)
        other = User.objects.create_user("director")
        chosen.set_approver(self.hr, self.co, other)
        self.assertTrue(approvals.can_act(self.approval(req), other))
        self.assertFalse(approvals.can_act(self.approval(req), self.investor))
        self.assertEqual(ApprovalStep.objects.filter(flow__company=self.co, flow__code=svc.FLOW_CODE).count(), 1)


class NobodyElseTests(ApproverCase):
    def test_the_named_person_cannot_be_asked_to_decide_their_own_request(self):
        Employee.objects.filter(pk=self.emp.pk).update(user=self.investor)
        chosen.set_approver(self.hr, self.co, self.investor)
        with self.assertRaisesMessage(svc.StatusError, "designation"):
            svc.ask(self.emp, requested_by=self.hr)
        self.assertFalse(FamilyStatusRequest.objects.exists())
        self.assertFalse(ApprovalRequest.objects.filter(flow__code=svc.FLOW_CODE).exists())

    def test_the_named_person_cannot_send_what_they_raised_themselves(self):
        chosen.set_approver(self.hr, self.co, self.hr)                       # HR is both the sender and the approver
        with self.assertRaisesMessage(svc.StatusError, "nobody can approve a request they raised"):
            svc.ask(self.emp, requested_by=self.hr)
        req = svc.ask(self.emp, requested_by=self.login)                     # the employee asks: waiting for HR
        with self.assertRaisesMessage(svc.StatusError, "raised"):
            svc.forward(req, self.hr)
        req.refresh_from_db()
        self.assertEqual(req.status, Status.WAITING_HR)                      # nothing half-done


class SettingTests(ApproverCase):
    def test_going_back_to_the_reporting_manager_keeps_the_default_for_new_requests(self):
        chosen.set_approver(self.hr, self.co, self.investor)
        self.assertEqual(chosen.approver_for(self.co), self.investor)
        self.assertTrue(chosen.clear_approver(self.hr, self.co))
        self.assertIsNone(chosen.approver_for(self.co))
        self.assertFalse(chosen.clear_approver(self.hr, self.co))             # nothing more to clear
        req = svc.ask(self.emp, requested_by=self.hr)
        self.assertTrue(approvals.can_act(self.approval(req), self.boss))
        with self.assertRaisesMessage(svc.StatusError, "reporting manager"):
            svc.ask(self.emp2, requested_by=self.hr)                          # no manager, no named person

    def test_a_named_person_can_be_set_again_after_going_back(self):
        chosen.set_approver(self.hr, self.co, self.investor)
        chosen.clear_approver(self.hr, self.co)
        chosen.set_approver(self.hr, self.co, self.investor)
        self.assertEqual(chosen.approver_for(self.co), self.investor)
        self.assertEqual(ApprovalFlow.objects.filter(company=self.co, code=svc.FLOW_CODE).count(), 1)

    def test_another_company_is_not_affected(self):
        other = Company.objects.create(code="C9", name="Other Company")
        chosen.set_approver(self.hr, self.co, self.investor)
        self.assertIsNone(chosen.approver_for(other))

    def test_someone_who_cannot_sign_in_cannot_be_chosen(self):
        gone = User.objects.create_user("gone", is_active=False)
        for who in (gone, None):
            with self.assertRaisesMessage(chosen.ApproverError, "can sign in"):
                chosen.set_approver(self.hr, self.co, who)

    def test_steps_someone_else_set_up_are_never_overwritten(self):
        flow = ApprovalFlow.objects.create(company=self.co, code=svc.FLOW_CODE, name="Custom")
        ApprovalStep.objects.create(flow=flow, order=1, name="Reporting manager", approver_type="manager")
        with self.assertRaisesMessage(chosen.ApproverError, "set up elsewhere"):
            chosen.set_approver(self.hr, self.co, self.investor)
        self.assertEqual(flow.steps.get().approver_type, "manager")

    def test_the_change_is_audited(self):
        from apps.audit.models import AuditEvent
        chosen.set_approver(self.hr, self.co, self.investor)
        self.assertTrue(AuditEvent.objects.filter(module="dependants", reason__contains="Ibrahim").exists())
