from datetime import timedelta
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile

from apps.audit.models import AuditEvent
from apps.compliance import services as compliance
from apps.compliance.models import Document
from apps.core import approvals
from apps.core.models import ApprovalFlow
from apps.employees.models import Employee

from . import services as family
from . import visa_services as visa
from .defaults import FLOW_CODE
from .models import DependantAttachment
from .testing import VisaCase
from .visa import FamilyVisaRequest, VisaPayment

Kind = FamilyVisaRequest.Kind


class FlowTests(VisaCase):
    def test_a_default_flow_exists_after_migrate(self):
        flow = ApprovalFlow.objects.get(code=FLOW_CODE, company__isnull=True)
        steps = list(flow.steps.order_by("order"))
        self.assertEqual([(s.order, s.approver_type) for s in steps], [(1, "manager"), (2, "group")])
        self.assertEqual(steps[1].group.name, "HR")

    def test_group_permissions_exist_after_migrate(self):
        self.assertTrue(self.hr.has_perm("dependants.add_familyvisarequest"))
        self.assertTrue(self.hr.has_perm("dependants.add_visapayment"))
        self.assertTrue(self.finance.has_perm("dependants.add_visapayment"))
        self.assertFalse(self.finance.has_perm("dependants.add_familyvisarequest"))
        self.assertTrue(self.boss.has_perm("dependants.view_familyvisarequest"))
        self.assertFalse(self.boss.has_perm("dependants.change_familyvisarequest"))


class RequestRulesTests(VisaCase):
    def test_each_kind_has_its_rules(self):
        with self.assertRaisesMessage(visa.VisaError, "no current family visa"):
            self.ask(Kind.RENEWAL)
        with self.assertRaisesMessage(visa.VisaError, "no current family visa"):
            self.ask(Kind.CANCELLATION, reason="Going home")
        self.make_visa()
        with self.assertRaisesMessage(visa.VisaError, "already has a current family visa"):
            self.ask(Kind.NEW)
        with self.assertRaisesMessage(visa.VisaError, "Give the reason"):
            self.ask(Kind.CANCELLATION)
        with self.assertRaisesMessage(visa.VisaError, "Choose what"):
            self.ask("nonsense")
        self.assertFalse(FamilyVisaRequest.objects.exists())
        self.assertEqual(self.ask(Kind.RENEWAL).status, "pending")

    def test_one_request_at_a_time_and_none_for_someone_no_longer_sponsored(self):
        self.ask()
        with self.assertRaisesMessage(visa.VisaError, "already has a visa request in progress"):
            self.ask()
        gone = self.person("Layla")
        family.deactivate_dependant(gone)
        with self.assertRaisesMessage(visa.VisaError, "no longer sponsored"):
            self.ask(dependant=gone)

    def test_a_sponsor_who_has_left_can_only_have_the_visa_cancelled(self):
        self.make_visa()
        Employee.objects.filter(pk=self.emp.pk).update(status="separated")
        self.kid.refresh_from_db()
        self.assertEqual(visa.allowed_kinds(self.kid), [Kind.CANCELLATION])
        with self.assertRaisesMessage(visa.VisaError, "has left the company"):
            self.ask(Kind.RENEWAL)
        self.assertEqual(self.ask(Kind.CANCELLATION, reason="Sponsor has left").status, "pending")

    def test_allowed_kinds_follow_the_visa_and_the_open_request(self):
        self.assertEqual(visa.allowed_kinds(self.kid), [Kind.NEW])
        self.make_visa()
        self.assertEqual(visa.allowed_kinds(self.kid), [Kind.RENEWAL, Kind.CANCELLATION])
        self.ask(Kind.RENEWAL)
        self.assertEqual(visa.allowed_kinds(self.kid), [])

    def test_no_reporting_manager_means_no_request_and_a_clear_message(self):
        noor = self.person("Noor", employee=self.emp2, relationship="spouse")
        with self.assertRaisesMessage(visa.VisaError, "reporting manager"):
            self.ask(dependant=noor)
        self.assertFalse(FamilyVisaRequest.objects.exists())

    def test_asking_is_audited_against_the_sponsor(self):
        self.ask()
        self.assertTrue(AuditEvent.objects.filter(module="dependants", action="create",
                                                  subject_employee_id=self.emp.pk,
                                                  company_id=self.co.pk).exists())


class ApprovalTests(VisaCase):
    def test_both_approvals_are_needed_and_the_person_who_asked_cannot_give_them(self):
        req = self.ask()
        a = self.approval_of(req)
        with self.assertRaises(approvals.ApprovalError):                       # HR cannot skip the manager
            approvals.decide(a.pk, self.hr_b, "approve")
        approvals.decide(a.pk, self.boss, "approve")
        req.refresh_from_db()
        self.assertEqual(req.status, "pending")
        with self.assertRaises(approvals.ApprovalError):                       # the HR user who asked
            approvals.decide(a.pk, self.hr, "approve")
        approvals.decide(a.pk, self.hr_b, "approve")
        req.refresh_from_db()
        self.assertEqual(req.status, "approved")

    def test_rejection_reaches_the_request(self):
        req = self.ask()
        approvals.decide(self.approval_of(req).pk, self.boss, "reject", comment="Not now")
        req.refresh_from_db()
        self.assertEqual(req.status, "rejected")
        self.assertEqual(visa.allowed_kinds(self.kid), [Kind.NEW])             # free to ask again

    def test_only_the_person_who_asked_can_withdraw(self):
        req = self.ask()
        with self.assertRaises(visa.VisaError):
            visa.cancel_request(req, self.hr_b)
        self.assertEqual(visa.cancel_request(req, self.hr).status, "cancelled")
        with self.assertRaises(visa.VisaError):                                # already finished
            visa.cancel_request(req, self.hr)

    def test_finishing_the_approval_twice_changes_nothing(self):
        req = self.approve(self.ask())
        visa.mark_submitted(req, submitted_on=self.today)
        visa.finalize(self.approval_of(req))
        req.refresh_from_db()
        self.assertEqual(req.status, "submitted")


class ProcessingTests(VisaCase):
    def test_a_new_visa_becomes_a_tracked_document(self):
        req = self.ask()
        with self.assertRaisesMessage(visa.VisaError, "approved"):
            visa.complete(req, self.hr, completed_on=self.today, expiry_date=self.today + timedelta(days=700))
        req = self.approve(req)
        with self.assertRaisesMessage(visa.VisaError, "expiry date"):
            visa.complete(req, self.hr, completed_on=self.today)
        with self.assertRaisesMessage(visa.VisaError, "future"):
            visa.complete(req, self.hr, completed_on=self.today + timedelta(days=1),
                          expiry_date=self.today + timedelta(days=700))
        visa.mark_submitted(req, submitted_on=self.today, reference="NPRA-1")
        req = visa.complete(req, self.hr, completed_on=self.today, number="FV-9", issue_date=self.today,
                            expiry_date=self.today + timedelta(days=730))
        doc = Document.objects.get(dependant=self.kid, document_type=self.visa_type, is_current=True)
        self.assertEqual((req.status, req.visa_document_id, doc.number, doc.employee_id, doc.company_id),
                         ("completed", doc.pk, "FV-9", self.emp.pk, self.co.pk))
        self.assertEqual(req.reference, "NPRA-1")
        with self.assertRaises(visa.VisaError):                                 # only once
            visa.complete(req, self.hr, completed_on=self.today, expiry_date=self.today + timedelta(days=900))

    def test_submitting_needs_an_approved_request(self):
        req = self.ask()
        with self.assertRaises(visa.VisaError):
            visa.mark_submitted(req, submitted_on=self.today)

    def test_a_renewal_keeps_the_history_and_the_family_member(self):
        old = self.make_visa(days=10, number="OLD-1")
        req = self.approve(self.ask(Kind.RENEWAL))
        visa.complete(req, self.hr, completed_on=self.today, expiry_date=self.today + timedelta(days=740))
        new = Document.objects.get(dependant=self.kid, document_type=self.visa_type, is_current=True)
        old.refresh_from_db()
        self.assertEqual((new.previous, new.number, new.dependant, old.is_current), (old, "OLD-1", self.kid, False))

    def test_a_cancellation_retires_the_visa_and_stops_the_chasing(self):
        visa_doc = self.make_visa(days=200)
        passport = Document.objects.create(dependant=self.kid, document_type=self.dpassport, responsible=self.pro,
                                           expiry_date=self.today + timedelta(days=20))
        task = compliance.open_renewal(passport)
        req = self.approve(self.ask(Kind.CANCELLATION, reason="Returning home"))
        visa.complete(req, self.hr, completed_on=self.today)
        for obj in (visa_doc, task, self.kid):
            obj.refresh_from_db()
        self.assertEqual((visa_doc.is_current, task.status, self.kid.is_active, self.kid.left_on),
                         (False, "cancelled", False, self.today))

    def test_the_checklist_shows_what_is_on_file(self):
        items = {i["label"]: i["have"] for i in visa.checklist(self.kid)}
        self.assertEqual(items["Birth certificate"], False)
        self.assertNotIn("Family Visa (Residence Permit)", items)
        DependantAttachment.objects.create(dependant=self.kid, kind="birth_certificate",
                                           file=SimpleUploadedFile("b.pdf", b"%PDF-1.4", content_type="application/pdf"))
        Document.objects.create(dependant=self.kid, document_type=self.dpassport, responsible=self.pro,
                                expiry_date=self.today + timedelta(days=400))
        items = {i["label"]: i["have"] for i in visa.checklist(self.kid)}
        self.assertEqual((items["Birth certificate"], items[self.dpassport.name]), (True, True))


class PaymentTests(VisaCase):
    def test_costs_follow_the_request_and_the_report_groups_them(self):
        req = self.ask()
        with self.assertRaisesMessage(visa.VisaError, "once the request is approved"):
            visa.add_payment(req, VisaPayment(paid_on=self.today, government_fee=Decimal("10")))
        req = self.approve(req)
        with self.assertRaisesMessage(visa.VisaError, "greater than zero"):
            visa.add_payment(req, VisaPayment(paid_on=self.today))
        visa.add_payment(req, VisaPayment(paid_on=self.today, government_fee=Decimal("40.000"),
                                          charged_to_employee=True))
        visa.add_payment(req, VisaPayment(paid_on=self.today, service_fee=Decimal("10.500")))
        report = visa.cost_report(self.hr, self.today.year)
        self.assertEqual((report["grand"]["total"], report["grand"]["charged"]),
                         (Decimal("50.500"), Decimal("40.000")))
        self.assertEqual([r["kind"] for r in report["rows"]], ["New visa"])
        self.assertEqual(visa.payment_years(self.hr), [self.today.year])
        self.assertEqual(visa.cost_report(self.other_hr, self.today.year)["rows"], [])      # another company's costs

    def test_a_payment_can_follow_completion(self):
        req = self.approve(self.ask())
        visa.complete(req, self.hr, completed_on=self.today, number="FV-1",
                      expiry_date=self.today + timedelta(days=700))
        payment = visa.add_payment(req, VisaPayment(paid_on=self.today, government_fee=Decimal("25")))
        self.assertEqual((payment.company_id, payment.employee_id), (self.co.pk, self.emp.pk))
