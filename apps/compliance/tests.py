from datetime import date, timedelta
from decimal import Decimal
from unittest import mock

from django.core import mail
from django.core.management import call_command
from django.test import SimpleTestCase

from apps.audit.models import AuditEvent
from apps.compliance import schedule, services
from apps.compliance.defaults import ensure_system_defaults
from apps.compliance.models import (AlertLog, Document, DocumentType, RenewalPayment, RenewalTask)
from apps.compliance.testing import ComplianceCase
from apps.organization.models import Company


class ScheduleTests(SimpleTestCase):
    DAYS = [30, 14, 7, 1, 0]

    def bucket(self, left, repeat=7):
        return schedule.due_bucket(left, self.DAYS, repeat)

    def test_nothing_is_due_outside_the_first_window(self):
        self.assertIsNone(self.bucket(31))
        self.assertIsNone(self.bucket(400))

    def test_bucket_is_the_smallest_threshold_not_yet_passed(self):
        expected = {30: 30, 29: 30, 15: 30, 14: 14, 8: 14, 7: 7, 2: 7, 1: 1, 0: 0}
        for left, bucket in expected.items():
            self.assertEqual(self.bucket(left), bucket, f"{left} days left")

    def test_overdue_repeats_every_n_days(self):
        self.assertEqual([self.bucket(-d) for d in (1, 7, 8, 14, 15)], [-1, -1, -2, -2, -3])
        self.assertEqual([self.bucket(-d, repeat=3) for d in (1, 3, 4, 6, 7)], [-1, -1, -2, -2, -3])

    def test_switched_off_means_no_alerts_at_all(self):
        for left in (20, 0, -5):
            self.assertIsNone(schedule.due_bucket(left, [], 7))

    def test_state_is_derived_from_the_dates(self):
        self.assertEqual(schedule.state_for(-1, self.DAYS), "expired")
        self.assertEqual(schedule.state_for(0, self.DAYS), "due")
        self.assertEqual(schedule.state_for(30, self.DAYS), "due")
        self.assertEqual(schedule.state_for(31, self.DAYS), "valid")
        self.assertEqual(schedule.state_for(5, []), "valid")

    def test_alert_days_are_parsed_sorted_and_validated(self):
        self.assertEqual(schedule.normalise_alert_days("7, 30 ; 14 30"), [30, 14, 7])
        self.assertEqual(schedule.normalise_alert_days([1, "0"]), [1, 0])
        self.assertEqual(schedule.normalise_alert_days(""), [])
        for bad in ("abc", "30, -1", "400"):
            with self.assertRaises(ValueError):
                schedule.normalise_alert_days(bad)

    def test_add_months_clips_the_day(self):
        self.assertEqual(services.add_months(date(2026, 1, 31), 1), date(2026, 2, 28))
        self.assertEqual(services.add_months(date(2027, 11, 15), 3), date(2028, 2, 15))
        self.assertEqual(services.add_months(date(2026, 3, 10), 12), date(2027, 3, 10))


class DefaultsTests(ComplianceCase):
    def test_document_types_and_groups_exist_after_migrate(self):
        cr = DocumentType.objects.get(code="cr")
        self.assertEqual((cr.default_validity_months, cr.is_mandatory, cr.alert_days), (12, True, [30, 14, 7, 1, 0]))
        self.assertIn("السجل", cr.name_ar)
        self.assertEqual(DocumentType.objects.count(), 8)
        self.assertTrue(self.hr.has_perm("compliance.add_document"))
        self.assertTrue(self.finance.has_perm("compliance.add_renewalpayment"))
        self.assertFalse(self.finance.has_perm("compliance.add_document"))
        self.assertTrue(self.boss.has_perm("compliance.view_document"))
        self.assertFalse(self.boss.has_perm("compliance.add_renewalpayment"))

    def test_seeding_system_data_leaves_the_audit_trail_empty(self):
        # a fresh database must start with no audit rows from seeding, or any test that looks events up by id collides
        self.assertFalse(AuditEvent.objects.filter(module="compliance").exists())

    def test_rerunning_never_overwrites_edits(self):
        DocumentType.objects.filter(code="cr").update(alert_days=[60, 30], name="Our CR")
        ensure_system_defaults()
        ensure_system_defaults()
        cr = DocumentType.objects.get(code="cr")
        self.assertEqual((cr.alert_days, cr.name), ([60, 30], "Our CR"))
        self.assertEqual(DocumentType.objects.count(), 8)


class ScanTests(ComplianceCase):
    def scan(self, offset=0):
        return services.run_daily_scan(self.today + timedelta(days=offset))

    def recipients(self):
        return sorted(m.to[0] for m in mail.outbox)

    def test_first_alert_goes_to_responsible_hr_and_finance_and_opens_a_task(self):
        doc = self.make_doc(25)
        stats = self.scan()
        self.assertEqual((stats["alerts"], stats["emails"], stats["tasks"]), (1, 3, 1))
        self.assertEqual(self.recipients(), ["fin@example.com", "hr@example.com", "pro@example.com"])
        self.assertIn("expires in 25 days", mail.outbox[0].subject)
        self.assertIn("IV Spare Parts", mail.outbox[0].subject)
        task = doc.renewal_tasks.get()
        self.assertEqual((task.assignee, task.due_date, task.status), (self.pro, doc.expiry_date, "open"))
        self.assertEqual(list(doc.alerts.values_list("bucket", flat=True)), [30])

    def test_running_twice_the_same_day_sends_nothing_new(self):
        self.make_doc(25)
        self.scan()
        mail.outbox.clear()
        stats = self.scan()
        self.assertEqual((stats["alerts"], len(mail.outbox)), (0, 0))

    def test_full_lifecycle_sends_each_alert_once(self):
        doc = self.make_doc(25)
        sent = {}
        for offset in (0, 1, 20, 21, 24, 25, 26, 30, 33):
            sent[offset] = self.scan(offset)["alerts"]
        self.assertEqual({o: n for o, n in sent.items() if n}, {0: 1, 20: 1, 24: 1, 25: 1, 26: 1, 33: 1})
        self.assertEqual(sorted(doc.alerts.values_list("bucket", flat=True)), [-2, -1, 0, 1, 7, 30])
        self.assertEqual(doc.renewal_tasks.count(), 1)

    def test_missed_days_give_one_alert_not_a_flood(self):
        doc = self.make_doc(3)                       # first time the document is looked at, 3 days left
        self.assertEqual(self.scan()["alerts"], 1)
        self.assertEqual(list(doc.alerts.values_list("bucket", flat=True)), [7])

    def test_management_is_added_only_when_it_gets_urgent(self):
        self.make_doc(40)
        self.scan(10)                                # 30 days left -> normal
        self.assertNotIn("boss@example.com", self.recipients())
        mail.outbox.clear()
        self.scan(35)                                # 5 days left -> escalated
        self.assertIn("boss@example.com", self.recipients())

    def test_overdue_alert_says_overdue(self):
        self.make_doc(-3)
        self.scan()
        self.assertIn("OVERDUE", mail.outbox[0].subject)
        self.assertIn("expired 3 days ago", mail.outbox[0].subject)

    def test_other_companies_staff_are_not_emailed(self):
        self.make_doc(25, company=self.other_co, responsible=None)
        self.scan()
        self.assertEqual(self.recipients(), ["hr2@example.com"])

    def test_nobody_with_an_email_still_logs_the_alert(self):
        self.pro.email = ""
        self.pro.save()
        for u in (self.hr, self.finance):
            u.email = ""
            u.save()
        doc = self.make_doc(25)
        self.scan()
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(doc.alerts.get().recipients, 0)

    def test_email_failure_logs_nothing_and_is_retried(self):
        doc = self.make_doc(25)
        with mock.patch("apps.compliance.services.send_mass_mail", side_effect=OSError("smtp down")):
            with self.assertLogs("apps.compliance.services", level="ERROR"):
                stats = self.scan()
        self.assertEqual((stats["errors"], stats["alerts"]), (1, 0))
        self.assertFalse(doc.alerts.exists())
        self.assertFalse(doc.renewal_tasks.exists())          # rolled back together with the alert
        self.assertEqual(self.scan()["alerts"], 1)             # the next run delivers it
        self.assertTrue(doc.renewal_tasks.exists())

    def test_cancelled_renewal_is_not_reopened_by_later_alerts(self):
        doc = self.make_doc(25)
        self.scan()
        services.cancel_task(doc.renewal_tasks.get())
        self.scan(20)                                # a later alert is sent...
        self.assertEqual(doc.alerts.count(), 2)
        self.assertEqual(doc.renewal_tasks.count(), 1)         # ...but no new task appears

    def test_things_that_must_be_ignored(self):
        far = self.make_doc(200)
        old = self.make_doc(5, reference_name="old", is_current=False)
        inactive_type = DocumentType.objects.create(code="x", name="X", is_active=False)
        self.make_doc(5, dtype=inactive_type)
        inactive_co = Company.objects.create(code="Z", name="Dormant", is_active=False)
        self.make_doc(5, company=inactive_co)
        stats = self.scan()
        self.assertEqual((stats["alerts"], len(mail.outbox)), (0, 0))
        self.assertFalse(far.alerts.exists() or old.alerts.exists())

    def test_correcting_the_expiry_date_restarts_the_alerts(self):
        doc = self.make_doc(25)
        self.scan()
        doc.expiry_date = self.today + timedelta(days=300)
        services.update_document(doc, expiry_changed=True)
        self.assertFalse(doc.alerts.exists())
        doc.expiry_date = self.today + timedelta(days=25)
        services.update_document(doc, expiry_changed=True)
        mail.outbox.clear()
        self.assertEqual(self.scan()["alerts"], 1)

    def test_management_command(self):
        self.make_doc(25)
        call_command("run_compliance_scan", "--date", self.today.isoformat(), stdout=mock.MagicMock())
        self.assertEqual(AlertLog.objects.count(), 1)

    def test_celery_task_runs_the_scan(self):
        try:
            from apps.compliance.tasks import daily_scan
        except ModuleNotFoundError:
            self.skipTest("Celery is not installed")
        self.make_doc(25)
        self.assertEqual(daily_scan()["checked"], 1)


class RenewalAndPaymentTests(ComplianceCase):
    def test_renewing_keeps_history_and_closes_the_open_task(self):
        old = self.make_doc(10, number="123-1")
        task = services.open_renewal(old)
        new = services.renew_document(old, self.hr, expiry_date=old.expiry_date + timedelta(days=365),
                                      issue_date=self.today)
        old.refresh_from_db()
        task.refresh_from_db()
        self.assertEqual((old.is_current, new.is_current, new.previous), (False, True, old))
        self.assertEqual((new.number, new.company, new.responsible), ("123-1", self.co, self.pro))
        self.assertEqual((task.status, task.new_document), ("completed", new))
        self.assertEqual(new.older_versions(), [old])
        self.assertEqual(old.newer_version(), new)

    def test_renewal_rules(self):
        doc = self.make_doc(10)
        with self.assertRaises(services.ComplianceError):                  # must extend the validity
            services.renew_document(doc, self.hr, expiry_date=doc.expiry_date)
        services.renew_document(doc, self.hr, expiry_date=doc.expiry_date + timedelta(days=365))
        with self.assertRaises(services.ComplianceError):                  # already renewed
            services.renew_document(doc, self.hr, expiry_date=doc.expiry_date + timedelta(days=900))
        with self.assertRaises(services.ComplianceError):                  # only the current version
            services.open_renewal(doc)

    def test_only_one_current_document_per_type_and_reference(self):
        self.make_doc(100)
        with self.assertRaises(services.ComplianceError):
            services.create_document(self.hr, Document(company=self.co, document_type=self.cr,
                                                       expiry_date=self.today + timedelta(days=50)))
        vehicle = DocumentType.objects.get(code="vehicle-registration")      # named ones can coexist
        for plate in ("123456", "654321"):
            self.make_doc(100, dtype=vehicle, reference_name=plate)
        self.assertEqual(Document.objects.filter(document_type=vehicle).count(), 2)

    def test_open_renewal_rules(self):
        doc = self.make_doc(10)
        task = services.open_renewal(doc, estimated_cost=Decimal("75.000"))
        self.assertEqual((task.assignee, task.due_date), (self.pro, doc.expiry_date))
        with self.assertRaises(services.ComplianceError):
            services.open_renewal(doc)
        services.cancel_task(task)
        with self.assertRaises(services.ComplianceError):
            services.cancel_task(task)
        self.assertEqual(services.open_renewal(doc).status, "open")         # a cancelled one can be restarted

    def payment(self, **kwargs):
        defaults = dict(paid_on=self.today, government_fee=Decimal("0"), service_fee=Decimal("0"), fine=Decimal("0"))
        return RenewalPayment(**{**defaults, **kwargs})

    def test_payments_are_exact_to_the_fils_and_can_follow_completion(self):
        old = self.make_doc(10)
        task = services.open_renewal(old)
        services.record_payment(task, self.payment(government_fee=Decimal("50.100"), service_fee=Decimal("10.200"),
                                                   fine=Decimal("0.300"), description="CR fee"))
        services.renew_document(old, self.hr, expiry_date=old.expiry_date + timedelta(days=365))
        task.refresh_from_db()
        self.assertEqual(task.status, "completed")
        services.record_payment(task, self.payment(service_fee=Decimal("5.000")))   # a receipt that arrived late
        self.assertEqual(task.paid_total, Decimal("65.600"))
        self.assertEqual(task.payments.first().company, self.co)

    def test_payment_rules(self):
        task = services.open_renewal(self.make_doc(10))
        with self.assertRaises(services.ComplianceError):
            services.record_payment(task, self.payment())                          # all zero
        services.cancel_task(task)
        task.refresh_from_db()
        with self.assertRaises(services.ComplianceError):
            services.record_payment(task, self.payment(fine=Decimal("10")))

    def test_renewals_are_audited_with_the_company(self):
        doc = self.make_doc(10)
        services.renew_document(doc, self.hr, expiry_date=doc.expiry_date + timedelta(days=365))
        events = AuditEvent.objects.filter(module="compliance", company_id=self.co.pk)
        self.assertTrue(events.filter(action="create").exists())
        self.assertTrue(events.filter(action="update", object_id=str(doc.pk)).exists())


class DashboardAndReportTests(ComplianceCase):
    def test_documents_are_sorted_into_buckets(self):
        types = {c: DocumentType.objects.get(code=c) for c in
                 ("cr", "municipality-licence", "civil-defence", "insurance-policy", "lease-contract")}
        late = self.make_doc(-2)
        soon = self.make_doc(10, dtype=types["municipality-licence"])
        later = self.make_doc(45, dtype=types["civil-defence"])
        self.make_doc(200, dtype=types["insurance-policy"])
        data = services.dashboard(self.hr)
        self.assertEqual((data["overdue"], data["due"], data["upcoming"]), ([late], [soon], [later]))
        self.assertEqual(data["missing"], [])
        self.assertEqual(services.attention_count(self.hr), 2)

    def test_missing_mandatory_documents_are_flagged_per_company(self):
        self.assertEqual(services.dashboard(self.hr)["missing"], [(self.co, self.cr)])
        self.make_doc(100)
        self.assertEqual(services.dashboard(self.hr)["missing"], [])
        self.assertEqual(services.dashboard(self.other_hr)["missing"], [(self.other_co, self.cr)])

    def test_other_companies_documents_are_invisible(self):
        self.make_doc(-5, company=self.other_co)
        self.assertEqual(services.dashboard(self.hr)["overdue"], [])

    def test_cost_report_groups_and_hides_other_companies(self):
        doc = self.make_doc(5)
        task = services.open_renewal(doc)
        for gov, svc, fine in (("50.000", "10.500", "0"), ("20.000", "0", "20.000")):
            services.record_payment(task, RenewalPayment(
                paid_on=self.today, government_fee=Decimal(gov), service_fee=Decimal(svc), fine=Decimal(fine)))
        foreign = services.open_renewal(self.make_doc(5, company=self.other_co))
        services.record_payment(foreign, RenewalPayment(paid_on=self.today, government_fee=Decimal("999")))
        report = services.cost_report(self.hr, self.today.year)
        self.assertEqual(len(report["rows"]), 1)
        self.assertEqual((report["grand"]["government"], report["grand"]["service"], report["grand"]["fine"],
                          report["grand"]["total"]), (Decimal("70.000"), Decimal("10.500"), Decimal("20.000"),
                                                      Decimal("100.500")))
        self.assertEqual(services.payment_years(self.hr), [self.today.year])
