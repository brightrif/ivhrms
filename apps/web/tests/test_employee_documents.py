import re
from datetime import timedelta
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.template import RequestContext, Template
from django.test import RequestFactory
from django.urls import reverse

from apps.compliance import services
from apps.compliance.models import Document, DocumentType, RenewalPayment
from apps.compliance.testing import ComplianceCase
from apps.employees.models import Employee


def scan(name="scan.pdf"):
    return SimpleUploadedFile(name, b"%PDF-1.4 scan", content_type="application/pdf")


class EmployeeDocumentPageTests(ComplianceCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.hr)
        self.create = reverse("web:compliance_create")

    def iso(self, days):
        return (self.today + timedelta(days=days)).isoformat()

    def data(self, **over):
        data = {"employee": self.emp.pk, "document_type": self.residence.pk, "number": "R-1001",
                "issue_date": self.iso(-300), "expiry_date": self.iso(430), "responsible": self.pro.pk}
        data.update(over)
        return data

    def form(self, response):
        return response.context["form"]

    def html(self, name, *args, **query):
        return self.client.get(reverse(f"web:{name}", args=args), query).content.decode()

    # ------------------------------------------------------------ the two kinds of form

    def test_each_kind_of_form_offers_only_its_own_types_and_owner(self):
        company = self.form(self.client.get(self.create))
        self.assertNotIn("employee", company.fields)
        self.assertEqual({t.applies_to for t in company.fields["document_type"].queryset}, {"company"})
        person = self.form(self.client.get(self.create, {"kind": "employee"}))
        self.assertNotIn("company", person.fields)                          # an employee's company is the employee's
        self.assertEqual({t.applies_to for t in person.fields["document_type"].queryset}, {"employee"})
        self.assertEqual(set(person.fields["employee"].queryset), {self.emp, self.emp2})

    def test_adding_a_personal_document(self):
        url = f"{self.create}?kind=employee"
        r = self.client.post(url, {**self.data(), "file": scan()})
        doc = Document.objects.get(employee=self.emp)
        self.assertRedirects(r, reverse("web:compliance_detail", args=[doc.pk]))
        self.assertEqual((doc.company, doc.document_type, doc.responsible), (self.co, self.residence, self.pro))
        self.assertTrue(doc.file.name.startswith(f"compliance/documents/{self.co.pk}/"))
        page = self.client.get(reverse("web:compliance_detail", args=[doc.pk])).content.decode()
        self.assertIn("Ali Test", page)
        self.assertIn(reverse("web:employee_detail", args=[self.emp.pk]), page)       # links to the staff page

    def test_opened_from_the_staff_page_the_form_is_prefilled_and_goes_back_there(self):
        html = self.html("compliance_create", kind="employee", employee=self.emp.pk, type=self.passport.pk)
        self.assertRegex(html, rf'<option value="{self.emp.pk}" selected>')
        self.assertRegex(html, rf'<option value="{self.passport.pk}" selected>')
        self.assertIn(f'href="{reverse("web:employee_detail", args=[self.emp.pk])}">Cancel', html)

    def test_only_staff_you_can_see_who_are_still_here_can_be_chosen(self):
        url = f"{self.create}?kind=employee"
        r = self.client.post(url, self.data(employee=self.emp_other.pk))
        self.assertIn("employee", self.form(r).errors)
        Employee.objects.filter(pk=self.emp2.pk).update(status="separated")
        r = self.client.post(url, self.data(employee=self.emp2.pk))
        self.assertIn("employee", self.form(r).errors)
        self.assertFalse(Document.objects.exists())

    def test_a_second_current_document_of_the_same_type_names_the_person(self):
        url = f"{self.create}?kind=employee"
        self.client.post(url, self.data())
        again = self.client.post(url, self.data(number="R-2"))
        self.assertEqual(again.status_code, 200)
        self.assertIn("Ali Test already has a current document of this type", str(self.form(again).non_field_errors()))
        self.assertEqual(self.client.post(url, self.data(employee=self.emp2.pk)).status_code, 302)   # a colleague is fine

    def test_editing_cannot_change_who_or_what(self):
        doc = self.make_employee_doc(self.emp, 200, file=scan())
        url = reverse("web:compliance_edit", args=[doc.pk])
        self.assertContains(self.client.get(url), 'type="file"')                     # opens with a scan attached
        self.client.post(url, self.data(employee=self.emp2.pk, document_type=self.passport.pk, number="R-9",
                                        expiry_date=self.iso(500)))
        doc.refresh_from_db()
        self.assertEqual((doc.employee, doc.document_type, doc.number), (self.emp, self.residence, "R-9"))

    # ------------------------------------------------------------ lists, dashboard, coverage

    def test_the_list_can_be_narrowed_to_people_and_searched_by_name_or_number(self):
        company = self.make_doc(100)
        ali = self.make_employee_doc(self.emp, 100)
        sara = self.make_employee_doc(self.emp2, 100, dtype=self.passport)
        listing = reverse("web:compliance_documents")
        link = lambda d: f'href="{reverse("web:compliance_detail", args=[d.pk])}"'        # the table row, not the filters

        def shown(**query):
            page = self.client.get(listing, query).content.decode()
            return {d.pk for d in (company, ali, sara) if link(d) in page}

        self.assertEqual(shown(), {company.pk, ali.pk, sara.pk})
        self.assertEqual(shown(scope="employee"), {ali.pk, sara.pk})
        self.assertEqual(shown(scope="company"), {company.pk})
        self.assertEqual(shown(q="IV-0002"), {sara.pk})                                   # employee number
        self.assertEqual(shown(q="ali"), {ali.pk})                                        # employee name

    def test_former_staff_are_hidden_unless_asked_for(self):
        self.make_employee_doc(self.emp2, 100)
        Employee.objects.filter(pk=self.emp2.pk).update(status="separated")
        listing = reverse("web:compliance_documents")
        self.assertNotContains(self.client.get(listing), "Sara Test")
        self.assertContains(self.client.get(listing, {"former": "1"}), "Sara Test")

    def test_dashboard_has_a_column_for_each_and_the_coverage(self):
        self.make_doc(10)
        self.make_employee_doc(self.emp, 12)
        self.make_employee_doc(self.emp, 300, dtype=self.passport)
        page = self.client.get(reverse("web:compliance_dashboard")).content.decode()
        self.assertLess(page.index("Company documents"), page.index("Employee documents"))
        self.assertIn("Ali Test (IV-0001)", page)
        self.assertRegex(page, r"1 / 2")                                                   # the passport: 1 of 2 staff
        self.assertIn(reverse("web:compliance_missing", args=[self.passport.pk]), page)
        self.assertIn("?scope=employee", page)

    def test_the_missing_page_lists_who_has_no_document_and_lets_hr_judge(self):
        self.make_employee_doc(self.emp, 300, dtype=self.passport)
        url = reverse("web:compliance_missing", args=[self.passport.pk])
        page = self.client.get(url).content.decode()
        self.assertIn("Sara Test", page)
        self.assertIn("Bahraini", page)                                                    # nationality helps decide
        self.assertNotIn("Ali Test", page)
        self.assertNotIn("Zed Test", page)                                                 # another company's staff
        self.assertIn(f"kind=employee&amp;employee={self.emp2.pk}&amp;type={self.passport.pk}", page)
        self.assertEqual(self.client.get(reverse("web:compliance_missing", args=[self.cr.pk])).status_code, 404)
        self.client.force_login(self.nobody)
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_the_menu_badge_counts_personal_documents_too(self):
        self.make_doc(300, dtype=DocumentType.objects.get(code="cr"))
        self.assertEqual(self.client.get(reverse("web:compliance_count")).content, b"")
        self.make_employee_doc(self.emp, -2)
        self.assertContains(self.client.get(reverse("web:compliance_count")), ">1<")

    # ------------------------------------------------------------ the staff page card

    def card(self, user, employee):
        request = RequestFactory().get("/")
        request.user = user
        template = Template("{% load compliance_tags %}{% employee_documents employee %}")
        return template.render(RequestContext(request, {"employee": employee}))

    def test_staff_page_card(self):
        self.make_employee_doc(self.emp, 20, number="R-77")
        html = self.card(self.hr, self.emp)
        self.assertIn("Residence Permit", html)
        self.assertIn("R-77", html)
        self.assertIn(f"kind=employee&amp;employee={self.emp.pk}", html)                    # the Add button
        self.assertNotIn("Residence Permit", self.card(self.hr, self.emp2))                # only their own
        self.assertEqual(self.card(self.nobody, self.emp).strip(), "")                    # no permission: no card
        self.assertNotIn("Add document", self.card(self.boss, self.emp))                  # read-only role
        Employee.objects.filter(pk=self.emp.pk).update(status="separated")
        self.emp.refresh_from_db()
        self.assertNotIn("Add document", self.card(self.hr, self.emp))                    # nothing to add for a leaver

    # ------------------------------------------------------------ payments and costs

    def test_a_cost_can_be_charged_to_the_employee_only_on_their_own_documents(self):
        person = services.open_renewal(self.make_employee_doc(self.emp, 25))
        company = services.open_renewal(self.make_doc(25))
        self.assertIn('name="charged_to_employee"', self.html("compliance_payment_create", person.pk))
        self.assertNotIn("charged_to_employee", self.html("compliance_payment_create", company.pk))
        pay = {"paid_on": self.today.isoformat(), "government_fee": "40.000", "method": "bank_transfer",
               "charged_to_employee": "on", "paid_by": self.hr.pk}
        self.client.post(reverse("web:compliance_payment_create", args=[person.pk]), pay)
        self.assertTrue(RenewalPayment.objects.get().charged_to_employee)
        detail = self.client.get(reverse("web:compliance_detail", args=[person.document_id])).content.decode()
        self.assertIn("Charged to employee", detail)
        costs = self.client.get(reverse("web:compliance_costs")).content.decode()
        self.assertIn("Charged to staff", costs)
        self.assertEqual(costs.count("40.000"), 6)                                         # government, total, charged: row and totals

    # ------------------------------------------------------------ document types

    def test_types_page_shows_both_kinds_and_new_employee_types_work(self):
        page = self.client.get(reverse("web:compliance_types")).content.decode()
        company_part, employee_part = page.split("Employee documents</span>", 1)
        self.assertIn("Commercial Registration", company_part)
        self.assertNotIn("Passport", company_part)                                          # each kind has its own card
        self.assertIn("Passport", employee_part)
        self.assertIn("Residence Permit", employee_part)
        self.assertEqual(employee_part.count(">Required</span>"), 5)
        create = reverse("web:compliance_type_create")
        r = self.client.post(create, {"applies_to": "employee", "name": "Professional Licence",
                                      "alert_days": "90, 30", "overdue_repeat_days": "7"})
        self.assertEqual(r.status_code, 302)
        new = DocumentType.objects.get(code="professional-licence")
        self.assertEqual((new.applies_to, new.alert_days), ("employee", [90, 30]))
        self.assertContains(self.client.get(self.create, {"kind": "employee"}), "Professional Licence")
        self.assertNotContains(self.client.get(self.create), "Professional Licence")        # not for company documents
        # what a type belongs to is fixed once it exists
        self.client.post(reverse("web:compliance_type_edit", args=[new.pk]),
                         {"applies_to": "company", "name": "Professional Licence", "alert_days": "90, 30",
                          "overdue_repeat_days": "7"})
        new.refresh_from_db()
        self.assertEqual(new.applies_to, "employee")

    def test_a_deactivated_employee_type_stops_alerting_and_leaves_the_form(self):
        doc = self.make_employee_doc(self.emp, 25, dtype=self.passport)
        self.client.post(reverse("web:compliance_type_toggle", args=[self.passport.pk]))
        self.assertEqual(services.run_daily_scan(self.today)["alerts"], 0)
        self.assertNotIn(f'<option value="{self.passport.pk}"', self.html("compliance_create", kind="employee"))
        self.assertFalse(doc.alerts.exists())

    # ------------------------------------------------------------ layout rules

    def test_the_new_forms_follow_the_full_page_layout_and_show_every_field(self):
        doc = self.make_employee_doc(self.emp, 200, file=scan())
        task = services.open_renewal(doc)
        pages = {"add employee document": (reverse("web:compliance_create") + "?kind=employee"),
                 "edit employee document": reverse("web:compliance_edit", args=[doc.pk]),
                 "employee payment": reverse("web:compliance_payment_create", args=[task.pk]),
                 "employee renewal": reverse("web:compliance_task_create", args=[self.make_employee_doc(self.emp2, 90).pk]),
                 "new type": reverse("web:compliance_type_create"),
                 "edit employee type": reverse("web:compliance_type_edit", args=[self.passport.pk])}
        for label, url in pages.items():
            response = self.client.get(url)
            html = response.content.decode()
            self.assertIn('class="form-compact"', html, label)
            self.assertIn('class="form-actions"', html, label)
            self.assertRegex(html, r"col-(lg|xl)-\d+")
            for name in response.context["form"].fields:
                self.assertIn(f'name="{name}"', html, f"{label}: '{name}' is not on the page")
