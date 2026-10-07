from datetime import timedelta
from decimal import Decimal as D
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction

from . import accidents, attachments, financing, fines, fuel, maintenance, services
from .files import VehicleFile
from .incidents import Accident, Fine
from .test_upkeep import UpkeepCase

Kind = VehicleFile.Kind
PDF = b"%PDF-1.4 scan"


def pdf(name="scan.pdf"):
    return SimpleUploadedFile(name, PDF, content_type="application/pdf")


class FileCase(UpkeepCase):
    def build(self):
        v = self.vehicle()
        today = self.today
        entries = {
            "fuel": fuel.add_fill(v, filled_on=today, litres=D("10"), cost=D("5"), km=10),
            "service": maintenance.add_service(v, serviced_on=today, km=20),
            "fine": fines.save_fine(Fine(vehicle=v, fined_on=today, offence="Speeding", amount=D("20"), reference="T-1")),
            "accident": accidents.save_accident(Accident(vehicle=v, occurred_on=today, description="Scrape")),
            "loan": financing.create_loan(v, lender="Bank of Bahrain", financed_amount=D("5000"), installment_count=12,
                                          installment_amount=D("450"), first_due_on=today + timedelta(days=20)),
        }
        return v, entries


class AttachingTests(FileCase):
    def test_every_kind_of_entry_can_hold_a_file(self):
        v, entries = self.build()
        first_kind = {"fuel": Kind.RECEIPT, "service": Kind.INVOICE, "fine": Kind.TICKET,
                      "accident": Kind.POLICE_REPORT, "loan": Kind.CONTRACT}
        for name, entry in entries.items():
            f = attachments.attach(name, entry, kind=first_kind[name], file=pdf(), title="  First one ")
            self.assertEqual((f.target_kind, f.target, f.vehicle, f.company, f.title), (name, entry, v, self.co, "First one"))
            self.assertTrue(f.file.name.startswith(f"vehicles/{self.co.pk}/{v.pk}/") and f.file.name.endswith(".pdf"))
            with f.file.open("rb") as handle:
                self.assertEqual(handle.read(), PDF)
            self.assertEqual(list(attachments.files_of(entry)), [f])
            self.assertTrue(f.describe())

    def test_each_entry_only_takes_its_own_kinds(self):
        _, entries = self.build()
        for name, kind in (("fuel", Kind.TICKET), ("service", Kind.CONTRACT), ("fine", Kind.POLICE_REPORT),
                           ("accident", Kind.INVOICE), ("loan", Kind.RECEIPT)):
            with self.assertRaises(services.VehicleError, msg=name):
                attachments.attach(name, entries[name], kind=kind, file=pdf())
        attachments.attach("accident", entries["accident"], kind=Kind.PHOTO, file=pdf("crash.pdf"))
        attachments.attach("fine", entries["fine"], kind=Kind.RECEIPT, file=pdf())

    def test_the_same_type_and_size_rules_as_your_documents(self):
        _, entries = self.build()
        with self.assertRaises(services.VehicleError):
            attachments.attach("fuel", entries["fuel"], kind=Kind.RECEIPT,
                               file=SimpleUploadedFile("run.exe", b"MZ", content_type="application/octet-stream"))
        big = SimpleUploadedFile("big.pdf", b"0" * (10 * 1024 * 1024 + 1), content_type="application/pdf")
        with self.assertRaises(services.VehicleError) as ctx:
            attachments.attach("fuel", entries["fuel"], kind=Kind.RECEIPT, file=big)
        self.assertIn("larger than 10 MB", str(ctx.exception))
        self.assertFalse(VehicleFile.objects.exists())

    def test_a_cancelled_entry_takes_no_files(self):
        v, entries = self.build()
        fuel.void_fill(entries["fuel"], "entered twice")
        maintenance.void_service(entries["service"], "wrong vehicle")
        fines.void_fine(entries["fine"], "dismissed")
        accidents.void_accident(entries["accident"], "wrong vehicle")
        financing.void_loan(entries["loan"], "wrong vehicle")
        for name, entry in entries.items():
            entry.refresh_from_db()
            with self.assertRaises(services.VehicleError, msg=name):
                attachments.attach(name, entry, kind=Kind.OTHER, file=pdf())

    def test_a_limit_per_entry(self):
        _, entries = self.build()
        with mock.patch.object(attachments, "MAX_FILES", 2):
            attachments.attach("fuel", entries["fuel"], kind=Kind.RECEIPT, file=pdf())
            attachments.attach("fuel", entries["fuel"], kind=Kind.OTHER, file=pdf())
            with self.assertRaises(services.VehicleError):
                attachments.attach("fuel", entries["fuel"], kind=Kind.RECEIPT, file=pdf())
            attachments.attach("service", entries["service"], kind=Kind.INVOICE, file=pdf())     # another entry: fine

    def test_files_of_sold_vehicles_can_still_be_attached(self):
        v, entries = self.build()
        financing.settle_early(entries["loan"], self.today, D("5000"))
        services.mark_sold(v, self.today)
        self.assertTrue(attachments.attach("fine", entries["fine"], kind=Kind.TICKET, file=pdf()).pk)


class RemovingTests(FileCase):
    def test_removing_hides_the_file_but_keeps_it(self):
        _, entries = self.build()
        f = attachments.attach("fuel", entries["fuel"], kind=Kind.RECEIPT, file=pdf())
        with self.assertRaises(services.VehicleError):
            attachments.remove(f, " ")
        attachments.remove(f, "wrong receipt")
        self.assertEqual(list(attachments.files_of(entries["fuel"])), [])
        f.refresh_from_db()
        self.assertTrue(f.is_removed and f.file.storage.exists(f.file.name))              # kept on disk
        with self.assertRaises(services.VehicleError):
            attachments.remove(f, "again")

    def test_a_file_belongs_to_exactly_one_entry(self):
        v, entries = self.build()
        for owners in ({}, {"fuel_fill": entries["fuel"], "fine": entries["fine"]}):
            with self.assertRaises(IntegrityError), transaction.atomic():
                VehicleFile.objects.create(vehicle=v, kind=Kind.OTHER, file=pdf(), **owners)


class PermissionTests(FileCase):
    def test_who_may_look_at_and_change_which_files(self):
        view_and_change = {
            self.hr: {"fuel": (1, 1), "service": (1, 1), "fine": (1, 1), "accident": (1, 1), "loan": (0, 0)},
            self.finance: {"fuel": (1, 0), "service": (1, 0), "fine": (1, 1), "accident": (1, 0), "loan": (1, 1)},
            self.boss: {"fuel": (1, 0), "service": (1, 0), "fine": (1, 0), "accident": (1, 0), "loan": (1, 0)},
            self.nobody: {k: (0, 0) for k in ("fuel", "service", "fine", "accident", "loan")},
        }
        for user, expected in view_and_change.items():
            for name, (view, change) in expected.items():
                self.assertEqual((int(attachments.can_view(user, name)), int(attachments.can_change(user, name))),
                                 (view, change), (user.username, name))
