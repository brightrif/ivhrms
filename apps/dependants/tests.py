from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from apps.compliance import services as compliance
from apps.compliance.models import Document, DocumentType
from apps.employees.models import Employee
from apps.organization.models import Company

from . import services
from .models import Dependant


class DependantTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.co = Company.objects.create(code="IV", name="IV Spare Parts")
        self.emp = Employee.objects.create(company=self.co, employee_no="IV-0001", first_name="Ali",
                                           last_name="Test", nationality="Indian",
                                           joining_date=self.today - timedelta(days=400))
        make = DocumentType.objects.get_or_create
        self.passport = make(code="dependant-passport", defaults=dict(
            name="Dependant Passport", applies_to="dependant", is_mandatory=True))[0]
        self.visa = make(code="family-visa", defaults=dict(
            name="Family Visa", applies_to="dependant", is_mandatory=True))[0]
        self.emp_type = DocumentType.objects.create(code="t-emp-doc", name="T", applies_to="employee")

    def child(self, name="Sam", **kw):
        kw.setdefault("date_of_birth", self.today - timedelta(days=365 * 6 + 3))
        return services.add_dependant(None, self.emp, relationship="child", first_name=name, **kw)

    def doc(self, dependant, dtype=None, days=300):
        return Document(dependant=dependant, document_type=dtype or self.passport,
                        expiry_date=self.today + timedelta(days=days))

    def test_the_company_follows_the_employee(self):
        dep = self.child()
        self.assertEqual((dep.company_id, dep.employee_id), (self.co.pk, self.emp.pk))
        self.assertEqual(dep.age, 6)

    def test_a_child_needs_a_date_of_birth_and_it_cannot_be_in_the_future(self):
        with self.assertRaisesMessage(services.DependantError, "date of birth is required"):
            services.add_dependant(None, self.emp, relationship="child", first_name="Sam")
        with self.assertRaisesMessage(services.DependantError, "future"):
            self.child("Zed", date_of_birth=self.today + timedelta(days=1))

    def test_no_dependants_for_someone_who_has_left(self):
        self.emp.status = Employee.Status.SEPARATED
        self.emp.save()
        with self.assertRaisesMessage(services.DependantError, "left the company"):
            self.child()

    def test_the_same_family_member_cannot_be_added_twice(self):
        self.child("Sam")
        with self.assertRaises(services.DependantError):
            self.child("  Sam ", date_of_birth=Dependant.objects.get().date_of_birth)

    def test_a_dependant_document_takes_the_sponsors_employee_and_company(self):
        kid = self.child()
        doc = compliance.create_document(None, self.doc(kid))
        self.assertEqual((doc.employee_id, doc.company_id, doc.is_employee_document), (self.emp.pk, self.co.pk, False))
        self.assertIn("Sam", doc.subject_label)

    def test_the_kind_of_document_and_its_owner_must_match(self):
        kid = self.child()
        wrong = [self.doc(kid, self.emp_type),                                         # employee type on a dependant
                 Document(employee=self.emp, document_type=self.passport,              # dependant type, no dependant
                          expiry_date=self.today + timedelta(days=300))]
        for d in wrong:
            with self.assertRaises(compliance.ComplianceError):
                compliance.create_document(None, d)

    def test_each_child_has_their_own_current_passport_but_only_one(self):
        a, b = self.child("Sam"), self.child("Mia")
        compliance.create_document(None, self.doc(a))
        compliance.create_document(None, self.doc(b))                                  # a sibling's passport does not clash
        with self.assertRaises(compliance.ComplianceError):
            compliance.create_document(None, self.doc(a, days=200))
        self.assertEqual(Document.objects.filter(document_type=self.passport, is_current=True).count(), 2)

    def test_missing_documents_lists_the_mandatory_types_not_yet_held(self):
        kid = self.child()
        self.assertEqual({t.code for t in services.missing_documents(kid)},
                   {"dependant-passport", "dependant-cpr", "family-visa"})
        compliance.create_document(None, self.doc(kid))
        self.assertEqual({t.code for t in services.missing_documents(kid)}, {"dependant-cpr", "family-visa"})

    def test_deactivating_stamps_the_date_and_cannot_be_repeated(self):
        kid = services.deactivate_dependant(self.child())
        self.assertEqual((kid.is_active, kid.left_on), (False, self.today))
        with self.assertRaises(services.DependantError):
            services.deactivate_dependant(kid)
    def test_renewing_a_family_visa_keeps_the_dependant(self):
        a, b = self.child("Sam"), self.child("Mia")
        old_a = compliance.create_document(None, self.doc(a, self.visa, days=10))
        old_b = compliance.create_document(None, self.doc(b, self.visa, days=10))
        new_a = compliance.renew_document(old_a, None, expiry_date=old_a.expiry_date + timedelta(days=730))
        new_b = compliance.renew_document(old_b, None, expiry_date=old_b.expiry_date + timedelta(days=730))
        self.assertEqual((new_a.dependant, new_b.dependant), (a, b))
        self.assertEqual((new_a.employee_id, new_a.company_id), (self.emp.pk, self.co.pk))
        self.assertEqual({t.code for t in services.missing_documents(a)}, {"dependant-passport", "dependant-cpr"})

    def test_a_family_member_no_longer_sponsored_is_not_chased(self):
      kid = self.child()
      compliance.create_document(None, self.doc(kid, self.visa, days=25))
      Dependant.objects.filter(pk=kid.pk).update(is_active=False)
      self.assertEqual(compliance.run_daily_scan(self.today)["alerts"], 0)
      Dependant.objects.filter(pk=kid.pk).update(is_active=True)      # proves the exclusion is the cause
      self.assertEqual(compliance.run_daily_scan(self.today)["alerts"], 1)