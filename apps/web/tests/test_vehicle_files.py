from datetime import timedelta
from decimal import Decimal as D

from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.audit.models import AuditEvent
from apps.compliance.testing import ComplianceCase
from apps.vehicles import accidents, attachments, financing, fines, fuel, maintenance
from apps.vehicles.files import VehicleFile
from apps.vehicles.incidents import Accident, Fine
from apps.vehicles.models import Vehicle

PDF = b"%PDF-1.4 scan"
Kind = VehicleFile.Kind


def pdf(name="scan.pdf"):
    return SimpleUploadedFile(name, PDF, content_type="application/pdf")


class FilePageCase(ComplianceCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.hr)
        self.v = Vehicle.objects.create(company=self.co, plate_number="123456", make="Toyota", model="Hilux", year=2022)
        today = self.today
        self.entries = {
            "fuel": fuel.add_fill(self.v, filled_on=today, litres=D("10"), cost=D("5"), km=10),
            "service": maintenance.add_service(self.v, serviced_on=today, km=20),
            "fine": fines.save_fine(Fine(vehicle=self.v, fined_on=today, offence="Speeding", amount=D("20"), reference="T-1")),
            "accident": accidents.save_accident(Accident(vehicle=self.v, occurred_on=today, description="Scrape")),
            "loan": financing.create_loan(self.v, lender="Bank of Bahrain", financed_amount=D("5000"),
                                          installment_count=12, installment_amount=D("450"),
                                          first_due_on=today + timedelta(days=20)),
        }

    def url(self, name, *args):
        return reverse(f"web:{name}", args=args)

    def add(self, kind):
        return self.url("vehicle_file_add", kind, self.entries[kind].pk)

    def attach(self, kind, file_kind, title="Scan"):
        return attachments.attach(kind, self.entries[kind], kind=file_kind, file=pdf(), title=title)

    def back(self, kind):
        e = self.entries[kind]
        return {"fuel": self.url("vehicle_fuel", self.v.pk), "service": self.url("vehicle_service", self.v.pk),
                "fine": self.url("vehicle_incidents", self.v.pk), "accident": self.url("vehicle_accident", e.pk),
                "loan": self.url("vehicle_loan", self.v.pk) + f"?loan={e.pk}"}[kind]


class FilePageTests(FilePageCase):
    def test_attach_open_download_and_remove(self):
        add = self.add("fuel")
        self.assertEqual(self.client.get(add).status_code, 200)
        r = self.client.post(add, {"kind": "receipt", "title": "Bapco receipt", "file": pdf()})
        self.assertRedirects(r, self.back("fuel"))
        attached = VehicleFile.objects.get()
        self.assertEqual((attached.fuel_fill, attached.company, attached.kind), (self.entries["fuel"], self.co, "receipt"))
        self.assertContains(self.client.get(self.back("fuel")), "Bapco receipt")

        open_it = self.client.get(self.url("vehicle_file", attached.pk))
        self.assertEqual(open_it.status_code, 200)
        self.assertEqual(b"".join(open_it.streaming_content), PDF)
        self.assertFalse(open_it["Content-Disposition"].startswith("attachment"))
        self.assertEqual(open_it["X-Content-Type-Options"], "nosniff")
        saved = self.client.get(self.url("vehicle_file", attached.pk), {"download": "1"})
        self.assertTrue(saved["Content-Disposition"].startswith("attachment"))
        saved.close()
        open_it.close()

        remove = self.url("vehicle_file_remove", attached.pk)
        self.assertEqual(self.client.get(remove).status_code, 200)
        self.assertEqual(self.client.post(remove, {"reason": ""}).status_code, 200)
        self.assertRedirects(self.client.post(remove, {"reason": "wrong receipt"}), self.back("fuel"))
        self.assertNotContains(self.client.get(self.back("fuel")), "Bapco receipt")
        self.assertEqual(self.client.get(self.url("vehicle_file", attached.pk)).status_code, 404)

    def test_every_kind_of_entry_has_its_upload_page_and_comes_back_to_it(self):
        kinds = {"fuel": Kind.RECEIPT, "service": Kind.INVOICE, "fine": Kind.TICKET, "accident": Kind.POLICE_REPORT,
                 "loan": Kind.CONTRACT}
        for kind, file_kind in kinds.items():
            self.client.force_login(self.finance if kind == "loan" else self.hr)
            self.assertEqual(self.client.get(self.add(kind)).status_code, 200, kind)
            r = self.client.post(self.add(kind), {"kind": file_kind, "title": f"{kind} scan", "file": pdf()})
            self.assertRedirects(r, self.back(kind), msg_prefix=kind)
            self.assertContains(self.client.get(self.back(kind)), f"{kind} scan")
        self.assertEqual(VehicleFile.objects.count(), 5)

    def test_bad_uploads_are_form_errors_not_crashes(self):
        add = self.add("fuel")
        exe = SimpleUploadedFile("run.exe", b"MZ", content_type="application/octet-stream")
        big = SimpleUploadedFile("big.pdf", b"0" * (10 * 1024 * 1024 + 1), content_type="application/pdf")
        for data, field in (({"kind": "receipt", "file": exe}, "file"), ({"kind": "receipt", "file": big}, "file"),
                            ({"kind": "receipt"}, "file"), ({"kind": "ticket", "file": pdf()}, "kind")):
            r = self.client.post(add, data)
            self.assertEqual(r.status_code, 200, data)
            self.assertIn(field, r.context["form"].errors)
        self.assertFalse(VehicleFile.objects.exists())

    def test_a_cancelled_entry_takes_no_files(self):
        fuel.void_fill(self.entries["fuel"], "entered twice")
        self.assertRedirects(self.client.get(self.add("fuel")), self.back("fuel"), fetch_redirect_response=False)

    def test_the_pages_offer_the_attach_link_only_to_those_who_may_add(self):
        self.assertContains(self.client.get(self.back("fuel")), "Attach a file")
        self.assertContains(self.client.get(self.back("accident")), "Attach a file")
        self.client.force_login(self.boss)                                                  # may look, may not add
        self.assertNotContains(self.client.get(self.back("fuel")), "Attach a file")
        self.attach("fuel", Kind.RECEIPT, "Seen by management")
        self.assertContains(self.client.get(self.back("fuel")), "Seen by management")

    def test_the_files_page_shows_each_person_only_what_they_may_see(self):
        self.attach("fuel", Kind.RECEIPT, "Fuel receipt")
        self.attach("fine", Kind.TICKET, "The ticket")
        self.attach("loan", Kind.CONTRACT, "Loan contract")
        page = self.url("vehicle_files", self.v.pk)
        r = self.client.get(page)                                                           # HR
        self.assertContains(r, "Fuel receipt")
        self.assertContains(r, "The ticket")
        self.assertNotContains(r, "Loan contract")
        self.assertContains(r, "1 more file is only visible")
        self.client.force_login(self.finance)
        r = self.client.get(page)
        self.assertContains(r, "Loan contract")
        self.assertNotContains(r, "more file")
        self.client.force_login(self.other_hr)
        self.assertEqual(self.client.get(page).status_code, 404)

    def test_access_by_role_and_company(self):
        loan_file = self.attach("loan", Kind.CONTRACT, "Loan contract")
        fuel_file = self.attach("fuel", Kind.RECEIPT, "Fuel receipt")
        self.client.logout()
        self.assertEqual(self.client.get(self.url("vehicle_file", fuel_file.pk)).status_code, 302)   # sign in first
        expected = {                       # (open fuel file, open loan file, add to fuel, add to loan)
            self.hr: (200, 403, 200, 403), self.finance: (200, 200, 403, 200), self.boss: (200, 200, 403, 403),
            self.nobody: (403, 403, 403, 403), self.other_hr: (404, 404, 404, 403)}
        for user, (fuel_open, loan_open, fuel_add, loan_add) in expected.items():
            self.client.force_login(user)
            for url, status in ((self.url("vehicle_file", fuel_file.pk), fuel_open),
                                (self.url("vehicle_file", loan_file.pk), loan_open),
                                (self.add("fuel"), fuel_add), (self.add("loan"), loan_add)):
                r = self.client.get(url)
                if hasattr(r, "streaming_content"):
                    r.close()
                self.assertEqual(r.status_code, status, (user.username, url))
        self.client.force_login(self.hr)
        self.assertEqual(self.client.get(self.url("vehicle_file_add", "nonsense", 1)).status_code, 404)

    def test_the_forms_place_every_field_they_have(self):
        attached = self.attach("fuel", Kind.RECEIPT)
        for url in (self.add("fuel"), self.url("vehicle_file_remove", attached.pk)):
            r = self.client.get(url)
            placed = {n for section in r.context["layout"] for row in section["rows"] for n in row}
            self.assertEqual(placed, set(r.context["form"].fields), url)


class FileAuditTests(FilePageCase):
    """Uses your real audit log, which the other tests do not touch."""

    def test_every_view_and_download_is_audited(self):
        attached = self.attach("fuel", Kind.RECEIPT)
        for params in ({}, {"download": "1"}):
            r = self.client.get(self.url("vehicle_file", attached.pk), params)
            r.close()
        events = AuditEvent.objects.filter(action="download", object_id=str(attached.pk), actor=self.hr)
        self.assertEqual((events.count(), events.first().module, events.first().company_id), (2, "vehicles", self.co.pk))

    def test_attaching_and_removing_are_audited_too(self):
        self.client.post(self.add("fuel"), {"kind": "receipt", "file": pdf()})
        attached = VehicleFile.objects.get()
        self.assertTrue(AuditEvent.objects.filter(module="vehicles", action="create", object_id=str(attached.pk)).exists())
        self.client.post(self.url("vehicle_file_remove", attached.pk), {"reason": "wrong receipt"})
        self.assertTrue(AuditEvent.objects.filter(module="vehicles", action="update", object_id=str(attached.pk)).exists())

    def test_the_vehicle_page_links_to_the_files(self):
        self.assertContains(self.client.get(self.url("vehicle_detail", self.v.pk)),
                            self.url("vehicle_files", self.v.pk))
