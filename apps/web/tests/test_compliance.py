from datetime import timedelta
from decimal import Decimal

from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase
from django.urls import reverse

from apps.audit.models import AuditEvent
from apps.compliance import services
from apps.compliance.models import AlertLog, Document, DocumentType, RenewalPayment, RenewalTask
from apps.compliance.testing import ComplianceCase
from apps.web.templatetags.bs import status_badge

PDF = b"%PDF-1.4 scan"


def pdf(name="scan.pdf"):
    return SimpleUploadedFile(name, PDF, content_type="application/pdf")


class CompliancePageTests(ComplianceCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.hr)

    def iso(self, days):
        return (self.today + timedelta(days=days)).isoformat()

    def doc_data(self, **over):
        data = {"company": self.co.pk, "document_type": self.cr.pk, "number": "12345-1",
                "issue_date": self.iso(-300), "expiry_date": self.iso(65), "responsible": self.pro.pk,
                "agent_name": "Gulf PRO Services", "notes": ""}
        data.update(over)
        return data

    def form_of(self, response):
        return response.context["form"]

    # ------------------------------------------------------------ access

    def test_access_by_role(self):
        doc = self.make_doc(20)
        dash, create = reverse("web:compliance_dashboard"), reverse("web:compliance_create")
        self.client.logout()
        self.assertEqual(self.client.get(dash).status_code, 302)                   # sign in first
        for user, expected in ((self.nobody, 403), (self.hr, 200), (self.finance, 200), (self.boss, 200)):
            self.client.force_login(user)
            self.assertEqual(self.client.get(dash).status_code, expected, user.username)
        self.client.force_login(self.finance)
        self.assertEqual(self.client.get(create).status_code, 403)                  # finance cannot add documents
        self.assertEqual(self.client.get(reverse("web:compliance_edit", args=[doc.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse("web:compliance_types")).status_code, 403)
        task = services.open_renewal(doc)
        pay = reverse("web:compliance_payment_create", args=[task.pk])
        self.assertEqual(self.client.get(pay).status_code, 200)                     # but can record payments
        self.client.force_login(self.boss)
        self.assertEqual(self.client.get(pay).status_code, 403)                     # management is read-only

    def test_other_companies_are_invisible(self):
        foreign = self.make_doc(20, company=self.other_co)
        for name in ("compliance_detail", "compliance_edit", "compliance_renew", "compliance_file"):
            self.assertEqual(self.client.get(reverse(f"web:{name}", args=[foreign.pk])).status_code, 404, name)
        self.assertNotContains(self.client.get(reverse("web:compliance_documents")), "Second Co")
        task = services.open_renewal(foreign)
        self.assertEqual(self.client.get(reverse("web:compliance_payment_create", args=[task.pk])).status_code, 404)

    # ------------------------------------------------------------ pages render

    def test_every_page_renders_with_real_data(self):
        doc = self.make_doc(20, file=pdf())
        task = services.open_renewal(doc, estimated_cost=Decimal("60"))
        services.record_payment(task, RenewalPayment(paid_on=self.today, government_fee=Decimal("50"),
                                                     receipt=pdf("r.pdf"), description="CR fee"))
        self.make_doc(-3, dtype=DocumentType.objects.get(code="civil-defence"))
        urls = ["compliance_dashboard", "compliance_documents", "compliance_costs", "compliance_types",
                "compliance_create"]
        for name in urls:
            self.assertEqual(self.client.get(reverse(f"web:{name}")).status_code, 200, name)
        for name in ("compliance_detail", "compliance_edit", "compliance_renew", "compliance_task_create"):
            self.assertEqual(self.client.get(reverse(f"web:{name}", args=[doc.pk])).status_code, 200, name)
        # regression: the edit form must open for a document that already has a scan attached
        self.assertContains(self.client.get(reverse("web:compliance_edit", args=[doc.pk])), 'type="file"')
        detail = self.client.get(reverse("web:compliance_detail", args=[doc.pk]))
        for text in ("Renewal of the version", "CR fee", "50.000", "Receipt", "Alerts go to 30, 14, 7, 1, 0"):
            self.assertContains(detail, text)
        dash = self.client.get(reverse("web:compliance_dashboard"))
        self.assertContains(dash, "Civil Defence Certificate")
        self.assertContains(dash, "3 days ago")

    def test_forms_use_the_full_page_layout_and_show_every_field(self):
        doc = self.make_doc(20, file=pdf())
        task = services.open_renewal(doc)
        pages = {
            "add document": reverse("web:compliance_create"),
            "edit document": reverse("web:compliance_edit", args=[doc.pk]),
            "renew": reverse("web:compliance_renew", args=[doc.pk]),
            "start renewal": reverse("web:compliance_task_create", args=[doc.pk]),
            "payment": reverse("web:compliance_payment_create", args=[task.pk]),
            "add type": reverse("web:compliance_type_create"),
            "edit type": reverse("web:compliance_type_edit", args=[self.cr.pk]),
        }
        for label, url in pages.items():
            response = self.client.get(url)
            html = response.content.decode()
            self.assertIn('class="form-compact"', html, label)
            self.assertIn('class="form-actions"', html, label)          # Save stays pinned to the bottom
            self.assertRegex(html, r"col-(lg|xl)-\d+")                   # cards sit side by side on a wide screen
            self.assertEqual(html.count('class="row g-3"'), 1, label)
            for name in response.context["form"].fields:                 # a field missing from a layout would vanish
                self.assertIn(f'name="{name}"', html, f"{label}: '{name}' is not on the page")
        # the main form is two columns: Document on the left, Handling on the right
        html = self.client.get(pages["add document"]).content.decode()
        self.assertLess(html.index("col-lg-7"), html.index("col-lg-5"))
        self.assertLess(html.index('name="expiry_date"'), html.index('name="responsible"'))

    # ------------------------------------------------------------ creating, editing, files

    def test_create_with_a_scan_and_audited_download(self):
        r = self.client.post(reverse("web:compliance_create"), {**self.doc_data(), "file": pdf("cr 2026.pdf")})
        doc = Document.objects.get()
        self.assertRedirects(r, reverse("web:compliance_detail", args=[doc.pk]))
        self.assertEqual((doc.responsible, doc.created_by, doc.is_current), (self.pro, self.hr, True))
        self.assertTrue(doc.file.name.startswith(f"compliance/documents/{self.co.pk}/"))
        self.assertTrue(doc.file.name.endswith(".pdf"))
        self.assertNotIn("cr 2026", doc.file.name)                  # the stored name never reveals the original
        self.assertTrue(doc.file.storage.exists(doc.file.name))
        with self.assertRaises(ValueError):                         # there is no public address for the file
            doc.file.url

        url = reverse("web:compliance_file", args=[doc.pk])
        r = self.client.get(url)
        self.assertEqual(b"".join(r.streaming_content), PDF)
        r.close()
        self.assertEqual(r["Content-Type"], "application/pdf")
        self.assertTrue(r["Content-Disposition"].startswith("inline"))
        self.assertIn("commercial-registration-cr", r["Content-Disposition"])
        r = self.client.get(url + "?download=1")
        r.close()
        self.assertTrue(r["Content-Disposition"].startswith("attachment"))
        events = AuditEvent.objects.filter(action="download", object_id=str(doc.pk), actor=self.hr)
        self.assertEqual((events.count(), events.first().company_id), (2, self.co.pk))

        self.client.force_login(self.other_hr)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.client.force_login(self.nobody)
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_a_document_without_a_scan_has_no_download(self):
        doc = self.make_doc(20)
        self.assertEqual(self.client.get(reverse("web:compliance_file", args=[doc.pk])).status_code, 404)
        self.assertContains(self.client.get(reverse("web:compliance_detail", args=[doc.pk])), "No scan attached yet")

    def test_upload_rules(self):
        url = reverse("web:compliance_create")
        bad = SimpleUploadedFile("run.exe", b"MZ", content_type="application/octet-stream")
        r = self.client.post(url, {**self.doc_data(), "file": bad})
        self.assertEqual(r.status_code, 200)
        self.assertIn("file", self.form_of(r).errors)
        big = SimpleUploadedFile("big.pdf", b"0" * (10 * 1024 * 1024 + 1), content_type="application/pdf")
        r = self.client.post(url, {**self.doc_data(), "file": big})
        self.assertIn("larger than 10 MB", str(self.form_of(r).errors["file"]))
        self.assertFalse(Document.objects.exists())

    def test_validation_messages(self):
        url = reverse("web:compliance_create")
        r = self.client.post(url, self.doc_data(issue_date=self.iso(10), expiry_date=self.iso(5)))
        self.assertIn("expiry_date", self.form_of(r).errors)
        self.make_doc(100)
        r = self.client.post(url, self.doc_data())
        self.assertEqual(r.status_code, 200)
        self.assertIn("already has a current document", str(self.form_of(r).non_field_errors()))
        # a named one (e.g. a second vehicle) is fine
        plate = DocumentType.objects.get(code="insurance-policy")
        for ref in ("111111", "222222"):
            self.client.post(url, self.doc_data(document_type=plate.pk, reference_name=ref))
        self.assertEqual(Document.objects.filter(document_type=plate).count(), 2)

    def test_the_other_companys_users_cannot_be_chosen_as_responsible(self):
        r = self.client.post(reverse("web:compliance_create"), self.doc_data(responsible=self.other_hr.pk))
        self.assertIn("responsible", self.form_of(r).errors)

    def test_edit_locks_company_and_type_and_restarts_alerts_when_the_date_moves(self):
        doc = self.make_doc(20, file=pdf())
        AlertLog.objects.create(document=doc, bucket=30)
        old_file = doc.file.name
        url = reverse("web:compliance_edit", args=[doc.pk])
        data = self.doc_data(company=self.other_co.pk, document_type=DocumentType.objects.get(code="civil-defence").pk,
                             expiry_date=self.iso(20), number="NEW-9")
        self.client.post(url, data)
        doc.refresh_from_db()
        self.assertEqual((doc.company, doc.document_type, doc.number), (self.co, self.cr, "NEW-9"))
        self.assertEqual(doc.file.name, old_file)                       # no new upload keeps the existing scan
        self.assertTrue(doc.alerts.exists())                            # the date did not move
        self.client.post(url, self.doc_data(expiry_date=self.iso(90)))
        self.assertFalse(doc.alerts.exists())                           # the date moved, so alerts restart
        self.assertEqual(doc.__class__.objects.get(pk=doc.pk).expiry_date, self.today + timedelta(days=90))

    # ------------------------------------------------------------ renewing

    def test_renew_flow_keeps_history(self):
        doc = self.make_doc(10, file=pdf())
        url = reverse("web:compliance_renew", args=[doc.pk])
        page = self.client.get(url)
        suggested = services.add_months(doc.expiry_date, 12).isoformat()
        self.assertContains(page, f'value="{suggested}"')               # the usual validity is pre-filled

        bad = self.client.post(url, {"expiry_date": doc.expiry_date.isoformat(), "number": "X"})
        self.assertIn("Must be later", str(self.form_of(bad).errors["expiry_date"]))

        r = self.client.post(url, {"expiry_date": suggested, "number": "12345-2",
                                   "issue_date": self.today.isoformat(), "file": pdf("new.pdf")})
        new = Document.objects.get(is_current=True)
        self.assertRedirects(r, reverse("web:compliance_detail", args=[new.pk]))
        doc.refresh_from_db()
        self.assertEqual((doc.is_current, new.previous, new.number), (False, doc, "12345-2"))
        self.assertTrue(new.file.name and new.file.name != doc.file.name)

        old_page = self.client.get(reverse("web:compliance_detail", args=[doc.pk]))
        self.assertContains(old_page, "older version")
        self.assertNotContains(old_page, reverse("web:compliance_renew", args=[doc.pk]))
        r = self.client.get(reverse("web:compliance_edit", args=[doc.pk]))
        self.assertRedirects(r, reverse("web:compliance_detail", args=[doc.pk]), fetch_redirect_response=False)
        self.assertContains(self.client.get(reverse("web:compliance_detail", args=[new.pk])), "Earlier versions")
        self.assertEqual(self.client.get(reverse("web:compliance_documents")).content.count(b"12345-2"), 1)
        self.assertContains(self.client.get(reverse("web:compliance_documents"), {"history": "1"}), "Older version")

    def test_renewal_task_and_payments_end_to_end(self):
        doc = self.make_doc(20)
        r = self.client.post(reverse("web:compliance_task_create", args=[doc.pk]), {
            "assignee": self.hr.pk, "due_date": self.iso(15), "estimated_cost": "75.000", "notes": "via Sijilat"})
        task = RenewalTask.objects.get()
        self.assertRedirects(r, reverse("web:compliance_detail", args=[doc.pk]))
        self.assertEqual((task.assignee, task.estimated_cost), (self.hr, Decimal("75.000")))
        again = self.client.post(reverse("web:compliance_task_create", args=[doc.pk]), {"due_date": self.iso(15)})
        self.assertIn("already open", str(self.form_of(again).non_field_errors()))

        pay = reverse("web:compliance_payment_create", args=[task.pk])
        zero = self.client.post(pay, {"paid_on": self.today.isoformat(), "method": "cash"})
        self.assertIn("greater than zero", str(self.form_of(zero).non_field_errors()))
        future = self.client.post(pay, {"paid_on": self.iso(3), "government_fee": "5", "method": "cash"})
        self.assertIn("paid_on", self.form_of(future).errors)

        self.client.force_login(self.finance)
        r = self.client.post(pay, {"description": "CR renewal fee", "paid_on": self.today.isoformat(),
                                   "government_fee": "50.000", "service_fee": "10.500", "fine": "0",
                                   "method": "bank_transfer", "reference": "TRX-889", "paid_by": self.finance.pk,
                                   "receipt": pdf("receipt.pdf")})
        self.assertRedirects(r, reverse("web:compliance_detail", args=[doc.pk]))
        payment = RenewalPayment.objects.get()
        self.assertEqual((payment.total, payment.company, payment.created_by), (Decimal("60.500"), self.co, self.finance))
        self.assertContains(self.client.get(reverse("web:compliance_detail", args=[doc.pk])), "60.500")

        receipt = self.client.get(reverse("web:compliance_receipt", args=[payment.pk]))
        self.assertEqual(b"".join(receipt.streaming_content), PDF)
        receipt.close()
        self.assertTrue(AuditEvent.objects.filter(action="download", object_id=str(payment.pk)).exists())
        self.assertContains(self.client.get(reverse("web:compliance_costs")), "60.500")

        self.client.force_login(self.hr)
        self.client.post(reverse("web:compliance_task_cancel", args=[task.pk]))
        task.refresh_from_db()
        self.assertEqual(task.status, "cancelled")
        self.assertEqual(self.client.get(pay).status_code, 200)         # the form opens but...
        blocked = self.client.post(pay, {"paid_on": self.today.isoformat(), "government_fee": "5", "method": "cash"})
        self.assertIn("cancelled", str(self.form_of(blocked).non_field_errors()))

    def test_payment_form_starts_with_today_and_the_current_user(self):
        task = services.open_renewal(self.make_doc(20))
        html = self.client.get(reverse("web:compliance_payment_create", args=[task.pk])).content.decode()
        self.assertIn(f'name="paid_on" value="{self.today.isoformat()}"', html)
        self.assertRegex(html, rf'<option value="{self.hr.pk}" selected>hr</option>')

    # ------------------------------------------------------------ document types

    def test_hr_manages_document_types(self):
        url = reverse("web:compliance_type_create")
        r = self.client.post(url, {"applies_to": "company", "name": "Fire Safety Permit", "alert_days": "7, 60, 30",
                                   "overdue_repeat_days": "5", "default_validity_months": "12"})
        self.assertRedirects(r, reverse("web:compliance_types"))
        fire = DocumentType.objects.get(code="fire-safety-permit")
        self.assertEqual((fire.alert_days, fire.overdue_repeat_days, fire.applies_to), ([60, 30, 7], 5, "company"))
        same_name = self.client.post(url, {"applies_to": "company", "name": "fire safety  permit", "overdue_repeat_days": "7"})
        self.assertIn("name", self.form_of(same_name).errors)            # two types may not share a name
        self.client.post(url, {"applies_to": "company", "name": "Fire-Safety Permit", "alert_days": "",
                               "overdue_repeat_days": "7"})
        second = DocumentType.objects.get(code="fire-safety-permit-2")    # same code, so it gets a suffix
        self.assertEqual(second.alert_days, [])                          # blank switches alerts off
        taken = self.client.post(url, {"applies_to": "company", "name": "Whatever", "code": "cr",
                                       "overdue_repeat_days": "7"})
        self.assertIn("code", self.form_of(taken).errors)
        bad = self.client.post(url, {"applies_to": "company", "name": "Broken", "alert_days": "soon",
                                     "overdue_repeat_days": "7"})
        self.assertIn("alert_days", self.form_of(bad).errors)

        edit = reverse("web:compliance_type_edit", args=[self.cr.pk])
        self.assertContains(self.client.get(edit), "30, 14, 7, 1, 0")
        self.client.post(edit, {"code": "hacked", "name": "Commercial Registration (CR)", "alert_days": "45, 10, 0",
                                "overdue_repeat_days": "7", "is_mandatory": "on"})
        self.cr.refresh_from_db()
        self.assertEqual((self.cr.code, self.cr.alert_days), ("cr", [45, 10, 0]))

        doc = self.make_doc(5, dtype=fire)
        self.client.post(reverse("web:compliance_type_toggle", args=[fire.pk]))
        fire.refresh_from_db()
        self.assertFalse(fire.is_active)
        create_page = self.client.get(reverse("web:compliance_create"))
        self.assertNotContains(create_page, ">Fire Safety Permit</option>")     # (the flash message repeats the name)
        self.assertContains(create_page, ">Fire-Safety Permit</option>")
        self.assertEqual(services.run_daily_scan()["alerts"], 0)         # a switched-off type sends nothing
        self.assertFalse(doc.alerts.exists())

    # ------------------------------------------------------------ badge, scheduler, colours

    def test_attention_badge(self):
        badge = reverse("web:compliance_count")
        self.assertContains(self.client.get(badge), ">1<")               # the required CR is missing
        doc = self.make_doc(-1)
        self.assertContains(self.client.get(badge), ">1<")               # now it is overdue instead
        doc.expiry_date = self.today + timedelta(days=200)
        doc.save()
        self.assertEqual(self.client.get(badge).content, b"")
        self.client.force_login(self.nobody)
        self.assertEqual(self.client.get(badge).status_code, 403)

    def test_expiry_alert_email_links_to_the_document(self):
        doc = self.make_doc(25)
        services.run_daily_scan()
        self.assertIn(reverse("web:compliance_detail", args=[doc.pk]), mail.outbox[0].body)
        self.assertIn("http://127.0.0.1:8000/", mail.outbox[0].body)

    def test_the_daily_job_is_scheduled_automatically(self):
        try:
            from config.celery import app
        except ModuleNotFoundError:
            self.skipTest("Celery is not installed")
        entry = app.conf.beat_schedule["compliance-daily-scan"]
        self.assertEqual(entry["task"], "apps.compliance.tasks.daily_scan")
        app.loader.import_default_modules()
        self.assertIn(entry["task"], app.tasks)


class BadgeColourTests(SimpleTestCase):
    def test_compliance_states_have_their_colours(self):
        for code, tone in (("expired", "danger"), ("due", "warning"), ("valid", "success"),
                           ("open", "primary"), ("completed", "success"), ("cancelled", "secondary")):
            self.assertIn(f"bg-{tone}-subtle", status_badge(code, code))
