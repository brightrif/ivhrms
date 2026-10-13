from datetime import timedelta

from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.audit.models import AuditEvent
from apps.compliance.models import Document, DocumentType
from apps.compliance.testing import ComplianceCase
from apps.dependants import services
from apps.dependants.models import Dependant, DependantAttachment


def pdf(name="scan.pdf"):
    return SimpleUploadedFile(name, b"%PDF-1.4 test", content_type="application/pdf")


class FamilyPageCase(ComplianceCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.hr)
        self.kid = services.add_dependant(self.hr, self.emp, relationship="child", first_name="Yusuf",
                                          date_of_birth=self.today - timedelta(days=2200))
        self.visa = DocumentType.objects.get(code="family-visa")
        self.dpassport = DocumentType.objects.get(code="dependant-passport")

    def url(self, name, *args):
        return reverse(f"web:{name}", args=args)

    def iso(self, days):
        return (self.today + timedelta(days=days)).isoformat()

    def make_visa(self, dependant=None, days=300):
        return Document.objects.create(dependant=dependant or self.kid, document_type=self.visa,
                                       expiry_date=self.today + timedelta(days=days), responsible=self.pro)


class AccessTests(FamilyPageCase):
    def test_who_can_look_and_who_can_change(self):
        list_url, add_url = self.url("employee_family"), self.url("employee_family_add")
        detail = self.url("employee_family_detail", self.kid.pk)
        self.client.logout()
        self.assertEqual(self.client.get(list_url).status_code, 302)                  # sign in first
        expected = {self.hr: (200, 200, 200), self.finance: (200, 403, 200), self.boss: (200, 403, 200),
                    self.nobody: (403, 403, 403), self.other_hr: (200, 200, 404)}
        for user, (look, add, one) in expected.items():
            self.client.force_login(user)
            self.assertEqual(self.client.get(list_url).status_code, look, user.username)
            self.assertEqual(self.client.get(add_url).status_code, add, user.username)
            self.assertEqual(self.client.get(detail).status_code, one, user.username)
        self.client.force_login(self.other_hr)
        self.assertEqual(self.client.get(self.url("employee_family_edit", self.kid.pk)).status_code, 404)
        self.assertNotContains(self.client.get(list_url), "Yusuf")                     # someone else's staff

    def test_the_tab_is_shown_only_to_people_who_may_use_it(self):
        self.assertContains(self.client.get(self.url("employee_family")), "Family &amp; Visas")
        self.client.force_login(self.nobody)
        self.assertEqual(self.client.get(self.url("employee_family")).status_code, 403)


class AddAndEditTests(FamilyPageCase):
    def data(self, **over):
        data = {"employee": self.emp.pk, "relationship": "child", "first_name": "Layla", "last_name": "",
                "date_of_birth": self.iso(-1000), "nationality": "Indian"}
        data.update(over)
        return data

    def test_hr_adds_a_family_member_and_lands_on_the_page(self):
        r = self.client.post(self.url("employee_family_add"), self.data())
        dep = Dependant.objects.get(first_name="Layla")
        self.assertRedirects(r, self.url("employee_family_detail", dep.pk))
        self.assertEqual((dep.employee, dep.company), (self.emp, self.co))

    def test_the_rules_are_shown_on_the_form(self):
        url = self.url("employee_family_add")
        r = self.client.post(url, self.data(date_of_birth=""))
        self.assertContains(r, "date of birth is required")
        r = self.client.post(url, self.data(first_name="Yusuf", date_of_birth=self.kid.date_of_birth.isoformat()))
        self.assertContains(r, "already recorded")
        self.assertEqual(Dependant.objects.count(), 1)

    def test_you_cannot_add_to_someone_elses_employee(self):
        self.client.force_login(self.other_hr)
        r = self.client.post(self.url("employee_family_add"), self.data())
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Dependant.objects.count(), 1)

    def test_editing_keeps_the_sponsor(self):
        url = self.url("employee_family_edit", self.kid.pk)
        self.assertNotIn("employee", self.client.get(url).context["form"].fields)
        r = self.client.post(url, {"relationship": "child", "first_name": "Yusuf", "last_name": "Test",
                                   "date_of_birth": self.kid.date_of_birth.isoformat(), "nationality": "Indian"})
        self.assertRedirects(r, self.url("employee_family_detail", self.kid.pk))
        self.kid.refresh_from_db()
        self.assertEqual((self.kid.last_name, self.kid.employee), ("Test", self.emp))


class ListTests(FamilyPageCase):
    def test_list_shows_the_sponsor_the_visa_and_what_is_missing(self):
        self.make_visa(days=20)
        r = self.client.get(self.url("employee_family"))
        self.assertContains(r, "Yusuf")
        self.assertContains(r, "Ali Test")
        self.assertContains(r, "20 days")
        self.assertContains(r, "missing")                         # passport and CPR are still to add

    def test_filters(self):
        other = services.add_dependant(self.hr, self.emp2, relationship="spouse", first_name="Noor")
        self.make_visa(days=20)
        self.make_visa(other, days=400)
        url = self.url("employee_family")
        r = self.client.get(url, {"expiring": "1"})
        self.assertContains(r, "Yusuf")
        self.assertNotContains(r, "Noor")
        r = self.client.get(url, {"relationship": "spouse"})
        self.assertContains(r, "Noor")
        self.assertNotContains(r, "Yusuf")
        r = self.client.get(url, {"q": "Sara"})                    # the sponsor's name finds the family
        self.assertContains(r, "Noor")
        self.assertNotContains(r, "Yusuf")

    def test_people_no_longer_sponsored_are_hidden_until_asked_for(self):
        services.deactivate_dependant(self.kid)
        url = self.url("employee_family")
        self.assertNotContains(self.client.get(url), "Yusuf")
        self.assertContains(self.client.get(url, {"status": "inactive"}), "Yusuf")
        self.assertContains(self.client.get(url, {"status": "all"}), "Yusuf")

    def test_htmx_gets_just_the_table(self):
        r = self.client.get(self.url("employee_family"), HTTP_HX_REQUEST="true")
        self.assertTemplateUsed(r, "web/family/_table.html")
        self.assertTemplateNotUsed(r, "web/family/list.html")


class DocumentTests(FamilyPageCase):
    def test_the_page_lists_missing_documents_and_a_document_can_be_added_from_it(self):
        detail = self.url("employee_family_detail", self.kid.pk)
        r = self.client.get(detail)
        for dtype in (self.visa, self.dpassport):
            self.assertContains(r, dtype.name)
        r = self.client.post(self.url("employee_family_document_add", self.kid.pk),
                             {"document_type": self.visa.pk, "number": "FV-1", "expiry_date": self.iso(300)})
        self.assertRedirects(r, detail)
        doc = Document.objects.get()
        self.assertEqual((doc.dependant, doc.employee, doc.company, doc.number), (self.kid, self.emp, self.co, "FV-1"))

    def test_a_type_they_already_hold_is_not_offered_again(self):
        self.make_visa()
        form = self.client.get(self.url("employee_family_document_add", self.kid.pk)).context["form"]
        self.assertNotIn(self.visa, form.fields["document_type"].queryset)
        r = self.client.post(self.url("employee_family_document_add", self.kid.pk),
                             {"document_type": self.visa.pk, "expiry_date": self.iso(300)})
        self.assertEqual((r.status_code, Document.objects.count()), (200, 1))

    def test_dates_are_checked(self):
        r = self.client.post(self.url("employee_family_document_add", self.kid.pk),
                             {"document_type": self.visa.pk, "issue_date": self.iso(-10), "expiry_date": self.iso(-20)})
        self.assertContains(r, "cannot be before")
        self.assertFalse(Document.objects.exists())

    def test_renewing_from_the_family_page_keeps_the_family_member(self):
        doc = self.make_visa(days=10)
        r = self.client.post(self.url("compliance_renew", doc.pk),
                             {"issue_date": self.iso(0), "expiry_date": self.iso(740)})
        self.assertEqual(r.status_code, 302)
        new = Document.objects.get(is_current=True)
        self.assertEqual((new.dependant, new.previous), (self.kid, doc))
        self.assertContains(self.client.get(self.url("employee_family_detail", self.kid.pk)), self.iso(740)[:4])

    def test_someone_no_longer_sponsored_takes_no_new_documents_and_cannot_be_marked_twice(self):
        self.assertEqual(self.client.get(self.url("employee_family_deactivate", self.kid.pk)).status_code, 405)
        self.client.post(self.url("employee_family_deactivate", self.kid.pk))
        self.kid.refresh_from_db()
        self.assertFalse(self.kid.is_active)
        r = self.client.get(self.url("employee_family_document_add", self.kid.pk))
        self.assertRedirects(r, self.url("employee_family_detail", self.kid.pk))
        r = self.client.post(self.url("employee_family_deactivate", self.kid.pk), follow=True)
        self.assertContains(r, "already inactive")


class PaperTests(FamilyPageCase):
    def add(self, **over):
        data = {"kind": "birth_certificate", "description": "Attested", "file": pdf()}
        data.update(over)
        return self.client.post(self.url("employee_family_paper_add", self.kid.pk), data)

    def test_hr_attaches_a_paper_and_every_download_is_audited(self):
        self.assertRedirects(self.add(), self.url("employee_family_detail", self.kid.pk))
        paper = DependantAttachment.objects.get()
        self.assertEqual((paper.dependant, paper.employee, paper.company), (self.kid, self.emp, self.co))
        for params in ({}, {"download": "1"}):
            r = self.client.get(self.url("employee_family_paper", paper.pk), params)
            self.assertEqual(r.status_code, 200)
            r.close()
        events = AuditEvent.objects.filter(action="download", object_id=str(paper.pk), actor=self.hr)
        self.assertEqual((events.count(), events.first().module, events.first().company_id), (2, "dependants", self.co.pk))

    def test_only_hr_sees_the_papers_and_only_in_its_own_company(self):
        self.add()
        paper = DependantAttachment.objects.get()
        url = self.url("employee_family_paper", paper.pk)
        expected = {self.finance: 403, self.boss: 403, self.nobody: 403, self.other_hr: 404}
        for user, status in expected.items():
            self.client.force_login(user)
            self.assertEqual(self.client.get(url).status_code, status, user.username)
        self.client.force_login(self.finance)
        self.assertNotContains(self.client.get(self.url("employee_family_detail", self.kid.pk)), "Papers")

    def test_upload_rules(self):
        r = self.add(file=SimpleUploadedFile("run.exe", b"MZ", content_type="application/octet-stream"))
        self.assertEqual(r.status_code, 200)
        self.assertIn("file", r.context["form"].errors)
        self.assertFalse(DependantAttachment.objects.exists())


class LayoutTests(FamilyPageCase):
    def test_every_form_places_every_field_it_has(self):
        pages = [self.url("employee_family_add"), self.url("employee_family_edit", self.kid.pk),
                 self.url("employee_family_document_add", self.kid.pk),
                 self.url("employee_family_paper_add", self.kid.pk)]
        for url in pages:
            r = self.client.get(url)
            self.assertEqual(r.status_code, 200, url)
            placed = {n for section in r.context["layout"] for row in section["rows"] for n in row}
            self.assertEqual(placed, set(r.context["form"].fields), url)
