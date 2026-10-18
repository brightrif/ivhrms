from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.accounts.models import User
from apps.core import approvals
from apps.core.models import ApprovalRequest
from apps.employees.models import Employee
from apps.organization.models import Designation

from . import eligibility
from . import status_services as svc
from . import visa_services as visa
from .models import Dependant, DependantAttachment
from .sponsor import FamilyVisaException
from .status import FamilyStatusRequest
from .testing import VisaCase

Status = FamilyStatusRequest.Status


class StatusCase(VisaCase):
    """The shared set-up allows its sponsors; these tests start from the real default (nobody has family status),
    and the employee has a login of their own."""

    def setUp(self):
        super().setUp()
        for sponsor in (self.emp, getattr(self, "emp2", None)):
            if sponsor is not None:
                eligibility.set_exception(sponsor, "default", "")
        self.login = User.objects.create_user("ali", email="ali@example.com")
        Employee.objects.filter(pk=self.emp.pk).update(user=self.login)
        self.emp.refresh_from_db()

    def sponsor(self):
        return Employee.objects.select_related("designation").get(pk=self.emp.pk)

    def status_approval(self, req):
        return ApprovalRequest.objects.get(object_id=str(req.pk), flow__code=svc.FLOW_CODE)

    def employee_asks(self, reason="The family will live here"):
        return svc.ask(self.emp, requested_by=self.login, reason=reason)


class StatusFlowTests(StatusCase):
    def test_a_default_flow_exists_after_migrate(self):
        from apps.core.models import ApprovalFlow
        flow = ApprovalFlow.objects.get(code=svc.FLOW_CODE, company__isnull=True)
        self.assertEqual([(s.order, s.approver_type) for s in flow.steps.order_by("order")], [(1, "manager")])

    def test_hr_can_run_the_requests_and_finance_cannot(self):
        self.assertTrue(self.hr.has_perm("dependants.change_familystatusrequest"))
        self.assertTrue(self.boss.has_perm("dependants.view_familystatusrequest"))
        self.assertFalse(self.boss.has_perm("dependants.change_familystatusrequest"))
        self.assertFalse(self.finance.has_perm("dependants.view_familystatusrequest"))

    def test_the_employee_asks_and_it_waits_for_hr(self):
        req = self.employee_asks()
        self.assertEqual((req.status, req.from_employee), (Status.WAITING_HR, True))
        self.assertFalse(ApprovalRequest.objects.filter(flow__code=svc.FLOW_CODE).exists())
        self.assertFalse(eligibility.sponsor_status(self.sponsor()).allowed)

    def test_hr_sends_it_on_and_the_manager_approves(self):
        req = self.employee_asks()
        self.assertEqual(svc.forward(req, self.hr).status, Status.PENDING)
        approvals.decide(self.status_approval(req).pk, self.boss, "approve", comment="Fine by me")
        req.refresh_from_db()
        self.assertEqual((req.status, req.decided_note), (Status.APPROVED, "Fine by me"))
        status = eligibility.sponsor_status(self.sponsor())
        self.assertEqual((status.allowed, status.source), (True, "exception"))
        self.assertTrue(status.reason.startswith("Approved by"))

    def test_approval_unlocks_the_family_visa(self):
        with self.assertRaisesMessage(visa.VisaError, "family status"):
            self.ask()
        req = self.employee_asks()
        svc.forward(req, self.hr)
        approvals.decide(self.status_approval(req).pk, self.boss, "approve")
        self.assertEqual(self.ask().status, "pending")

    def test_the_manager_can_reject(self):
        req = self.employee_asks()
        svc.forward(req, self.hr)
        approvals.decide(self.status_approval(req).pk, self.boss, "reject", comment="Not this year")
        req.refresh_from_db()
        self.assertEqual((req.status, req.decided_note), (Status.REJECTED, "Not this year"))
        self.assertFalse(eligibility.sponsor_status(self.sponsor()).allowed)

    def test_hr_asking_goes_straight_to_the_manager(self):
        req = svc.ask(self.emp, requested_by=self.hr, reason="")
        self.assertEqual((req.status, req.from_employee), (Status.PENDING, False))
        self.assertEqual(self.status_approval(req).status, "pending")

    def test_hr_can_decline_with_a_reason(self):
        req = self.employee_asks()
        with self.assertRaisesMessage(svc.StatusError, "reason"):
            svc.decline(req, "  ")
        req = svc.decline(req, "Not eligible yet")
        self.assertEqual((req.status, req.hr_note), (Status.DECLINED, "Not eligible yet"))
        self.assertEqual(self.employee_asks().status, Status.WAITING_HR)               # free to ask again

    def test_one_request_at_a_time_and_none_for_someone_who_already_has_status(self):
        self.employee_asks()
        with self.assertRaisesMessage(svc.StatusError, "already a family status request"):
            self.employee_asks()
        self.assertEqual(FamilyStatusRequest.objects.count(), 1)
        eligibility.set_exception(self.emp2, "allowed", "Approved")
        with self.assertRaisesMessage(svc.StatusError, "already has family status"):
            svc.ask(self.emp2, requested_by=self.hr)

    def test_a_ticked_designation_needs_no_request(self):
        d = Designation.objects.create(name="Manager", family_visa_default=True)
        Employee.objects.filter(pk=self.emp.pk).update(designation=d)
        with self.assertRaisesMessage(svc.StatusError, "already has family status"):
            self.employee_asks()

    def test_no_reporting_manager_means_no_request_and_a_clear_message(self):
        with self.assertRaisesMessage(svc.StatusError, "reporting manager"):
            svc.ask(self.emp2, requested_by=self.hr)
        self.assertFalse(FamilyStatusRequest.objects.filter(employee=self.emp2).exists())

    def test_the_employee_can_withdraw_while_it_waits_for_hr(self):
        req = self.employee_asks()
        with self.assertRaises(svc.StatusError):                                      # not someone else
            svc.withdraw(req, self.hr_b)
        self.assertEqual(svc.withdraw(req, self.login).status, Status.WITHDRAWN)

    def test_hr_can_withdraw_after_it_went_to_the_manager(self):
        req = self.employee_asks()
        svc.forward(req, self.hr)
        self.assertEqual(svc.withdraw(req, self.hr, hr=True).status, Status.WITHDRAWN)

    def test_asking_and_deciding_are_audited_against_the_employee(self):
        from apps.audit.models import AuditEvent
        self.employee_asks()
        self.assertTrue(AuditEvent.objects.filter(module="dependants", subject_employee_id=self.emp.pk,
                                                  company_id=self.co.pk).exists())

    def test_the_removal_of_status_blocks_new_visas_but_not_renewals(self):
        req = self.employee_asks()
        svc.forward(req, self.hr)
        approvals.decide(self.status_approval(req).pk, self.boss, "approve")
        self.make_visa()
        eligibility.set_exception(self.emp, "not_allowed", "Contract ended")
        self.assertEqual(self.ask(visa.Kind.RENEWAL).status, "pending")
        self.assertEqual(FamilyVisaException.objects.get(employee=self.emp).mode, "not_allowed")


class StatusPagesTests(StatusCase):
    def test_hr_sees_the_request_and_sends_it_on(self):
        req = self.employee_asks()
        self.client.force_login(self.hr)
        self.assertContains(self.client.get(reverse("web:employee_family_status")), self.emp.full_name)
        self.assertContains(self.client.get(reverse("web:employee_family_status_request", args=[req.pk])),
                            "Waiting for HR")
        resp = self.client.post(reverse("web:employee_family_status_forward", args=[req.pk]))
        self.assertEqual(resp.status_code, 302)
        req.refresh_from_db()
        self.assertEqual(req.status, Status.PENDING)

    def test_the_employee_sees_their_page_and_asks_from_it(self):
        self.client.force_login(self.login)
        self.assertContains(self.client.get(reverse("web:my_family")), "Request family status")
        resp = self.client.post(reverse("web:my_family_request"), {"reason": "Family joining"})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(FamilyStatusRequest.objects.get().status, Status.WAITING_HR)
        self.assertContains(self.client.get(reverse("web:my_family")), "HR is reviewing")

    def test_the_employee_cannot_use_the_hr_pages(self):
        req = self.employee_asks()
        self.client.force_login(self.login)
        self.assertEqual(self.client.post(reverse("web:employee_family_status_forward", args=[req.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse("web:employee_family_status")).status_code, 403)
        self.assertEqual(self.client.get(reverse("web:employee_family_add") + f"?employee={self.emp.pk}").status_code, 403)

    def test_the_employee_downloads_their_own_files_and_nobody_elses(self):
        att = DependantAttachment.objects.create(
            dependant=self.kid, kind="birth_certificate",
            file=SimpleUploadedFile("b.pdf", b"%PDF-1.4", content_type="application/pdf"))
        stranger = self.person("Noor", employee=self.emp2, relationship="spouse")
        other = DependantAttachment.objects.create(
            dependant=stranger, kind="marriage_certificate",
            file=SimpleUploadedFile("m.pdf", b"%PDF-1.4", content_type="application/pdf"))
        self.client.force_login(self.login)
        resp = self.client.get(reverse("web:my_family_file", args=["paper", att.pk]) + "?download=1")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp["Content-Disposition"].startswith("attachment"))
        b"".join(resp.streaming_content)
        self.assertEqual(self.client.get(reverse("web:my_family_file", args=["paper", other.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("web:my_family_file", args=["nonsense", att.pk])).status_code, 404)

    def test_the_employee_page_is_read_only(self):
        self.person("Zed")
        self.client.force_login(self.login)
        resp = self.client.get(reverse("web:my_family"))
        self.assertContains(resp, "read-only")
        self.assertNotContains(resp, "Add family member")
        self.assertEqual(Dependant.objects.filter(employee=self.emp).count(), 2)       # Yusuf and Zed, untouched
