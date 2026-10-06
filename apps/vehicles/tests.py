from datetime import timedelta

from django.core.exceptions import ValidationError

from apps.compliance import services as compliance_services
from apps.compliance.models import Document, DocumentType
from apps.compliance.tests import ComplianceCase

from . import services
from .models import Vehicle


class VehicleCase(ComplianceCase):
    def vehicle(self, plate="123456", **kw):
        data = dict(company=self.co, plate_number=plate, make="Toyota", model="Hilux", year=2022)
        data.update(kw)
        return Vehicle.objects.create(**data)

    def vehicle_doc(self, vehicle, code="vehicle-registration", days=100):
        doc = Document(document_type=DocumentType.objects.get(code=code), vehicle=vehicle,
                       expiry_date=self.today + timedelta(days=days))
        return compliance_services.create_document(self.hr, doc)


class VehicleTests(VehicleCase):
    def test_plate_is_normalised(self):
        self.assertEqual(self.vehicle("  ab   123 ").plate_number, "AB 123")

    def test_plate_is_unique_until_the_vehicle_is_sold(self):
        first = services.create_vehicle(self.hr, Vehicle(company=self.co, plate_number="555", make="Kia"))
        with self.assertRaises(services.VehicleError):
            services.create_vehicle(self.hr, Vehicle(company=self.co, plate_number="555", make="Kia"))
        services.mark_sold(first, self.today)
        again = services.create_vehicle(self.hr, Vehicle(company=self.co, plate_number="555", make="Kia"))
        self.assertEqual(Vehicle.objects.filter(plate_number="555").count(), 2)
        self.assertEqual(again.status, Vehicle.Status.ACTIVE)

    def test_selling_rules(self):
        v = self.vehicle()
        with self.assertRaises(services.VehicleError):                 # not in the future
            services.mark_sold(v, self.today + timedelta(days=1))
        services.mark_sold(v, self.today)
        with self.assertRaises(services.VehicleError):                 # only once
            services.mark_sold(v, self.today)


class VehicleDocumentTests(VehicleCase):
    def test_a_vehicle_document_takes_the_vehicles_company_and_plate(self):
        v = self.vehicle("777")
        doc = self.vehicle_doc(v)
        self.assertEqual(doc.company, self.co)
        self.assertEqual(doc.label, "Vehicle Registration - 777")

    def test_type_and_vehicle_must_match(self):
        v = self.vehicle()
        reg = DocumentType.objects.get(code="vehicle-registration")
        no_vehicle = Document(company=self.co, document_type=reg, expiry_date=self.today + timedelta(days=30))
        with self.assertRaises(ValidationError):
            no_vehicle.full_clean()
        company_type_on_vehicle = Document(company=self.co, document_type=self.cr, vehicle=v,
                                           expiry_date=self.today + timedelta(days=30))
        with self.assertRaises(ValidationError):
            company_type_on_vehicle.full_clean()

    def test_one_current_document_per_vehicle_and_type(self):
        a, b = self.vehicle("111"), self.vehicle("222")
        self.vehicle_doc(a)
        self.vehicle_doc(b)                                            # another vehicle is fine
        with self.assertRaises(compliance_services.ComplianceError):
            self.vehicle_doc(a)
        self.vehicle_doc(a, code="vehicle-insurance")                  # another type is fine

    def test_renewal_keeps_the_vehicle(self):
        v = self.vehicle()
        old = self.vehicle_doc(v, days=10)
        new = compliance_services.renew_document(old, self.hr, expiry_date=old.expiry_date + timedelta(days=365))
        self.assertEqual((new.vehicle, new.company, new.reference_name), (v, self.co, ""))

    def test_documents_of_sold_vehicles_stop_alerting(self):
        v = self.vehicle()
        doc = self.vehicle_doc(v, days=5)
        self.assertIn(doc, compliance_services.current_documents(self.hr))
        services.mark_sold(v, self.today)
        self.assertNotIn(doc, compliance_services.current_documents(self.hr))
