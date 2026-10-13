from datetime import timedelta
from io import StringIO

from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command

from apps.accounts.models import User
from apps.compliance.models import Document, DocumentType
from apps.configuration import registry
from apps.configuration import services as settings_service
from apps.employees.models import Employee

from . import leavers
from . import services as family
from . import visa_services as visa
from .models import DependantAttachment
from .testing import VisaCase
from .visa import FamilyLeaverAlert, FamilyVisaRequest

Kind = FamilyVisaRequest.Kind


class EligibilityTests(VisaCase):
    def test_the_starting_values(self):
        get = settings_service.get
        self.assertEqual([get("dependants.allow_spouse"), get("dependants.allow_children"),
                          get("dependants.allow_parents"), get("dependants.child_age_limit"),
                          get("dependants.require_papers")], [True, True, False, 18, False])

    def test_parents_are_not_put_forward_until_the_company_says_so(self):
        mona = self.person("Mona", relationship="parent")
        with self.assertRaisesMessage(visa.VisaError, "does not put forward parents"):
            self.ask(dependant=mona)
        settings_service.set_value("dependants.allow_parents", True, self.co)
        self.assertFalse(settings_service.get("dependants.allow_parents"))            # only this company changed
        self.assertEqual(self.ask(dependant=mona).status, "pending")

    def test_spouses_and_children_can_be_switched_off(self):
        settings_service.set_value("dependants.allow_children", False, self.co)
        with self.assertRaisesMessage(visa.VisaError, "does not put forward children"):
            self.ask()
        noor = self.person("Noor", relationship="spouse")
        settings_service.set_value("dependants.allow_spouse", False, self.co)
        with self.assertRaisesMessage(visa.VisaError, "does not put forward spouses"):
            self.ask(dependant=noor)

    def test_children_are_put_forward_only_up_to_the_age_limit(self):
        omar = family.add_dependant(self.hr, self.emp, relationship="child", first_name="Omar",
                                    date_of_birth=self.today - timedelta(days=365 * 20))
        with self.assertRaisesMessage(visa.VisaError, "puts children forward up to 18"):
            self.ask(dependant=omar)
        settings_service.set_value("dependants.child_age_limit", 0, self.co)           # 0 = no limit
        self.assertEqual(self.ask(dependant=omar).status, "pending")

    def test_cancelling_is_never_blocked_by_these_rules(self):
        mona = self.person("Mona", relationship="parent")
        self.make_visa(mona)
        self.assertEqual(self.ask(Kind.CANCELLATION, dependant=mona, reason="Going home").status, "pending")


class PapersFirstTests(VisaCase):
    def add_papers(self):
        DependantAttachment.objects.create(dependant=self.kid, kind="birth_certificate",
                                           file=SimpleUploadedFile("b.pdf", b"%PDF-1.4", content_type="application/pdf"))
        for code in ("dependant-passport", "dependant-cpr"):
            Document.objects.create(dependant=self.kid, document_type=DocumentType.objects.get(code=code),
                                    responsible=self.pro, expiry_date=self.today + timedelta(days=400))

    def test_off_by_default(self):
        req = self.approve(self.ask())
        self.assertEqual(visa.mark_submitted(req, submitted_on=self.today).status, "submitted")

    def test_on_it_holds_the_application_until_the_papers_are_on_file(self):
        settings_service.set_value("dependants.require_papers", True, self.co)
        req = self.approve(self.ask())
        with self.assertRaisesMessage(visa.VisaError, "Still missing"):
            visa.mark_submitted(req, submitted_on=self.today)
        with self.assertRaisesMessage(visa.VisaError, "Birth certificate"):            # not a way round it
            visa.complete(req, self.hr, completed_on=self.today, number="X", expiry_date=self.today + timedelta(days=700))
        self.add_papers()
        self.assertEqual(visa.mark_submitted(req, submitted_on=self.today).status, "submitted")
        self.assertEqual(visa.complete(req, self.hr, completed_on=self.today, number="X",
                                       expiry_date=self.today + timedelta(days=700)).status, "completed")

    def test_a_cancellation_is_never_held_up(self):
        settings_service.set_value("dependants.require_papers", True, self.co)
        self.make_visa()
        req = self.approve(self.ask(Kind.CANCELLATION, reason="Going home"))
        self.assertEqual(visa.complete(req, self.hr, completed_on=self.today).status, "completed")


class SettingsModuleTests(VisaCase):
    def test_the_module_describes_itself_and_every_right_it_offers_exists(self):
        from django.contrib.auth.models import Permission
        module = registry.get_module("dependants")
        self.assertEqual(module.label, "Family Visas")
        for cap in module.capabilities:
            app_label, codename = cap.perm.split(".", 1)
            self.assertTrue(Permission.objects.filter(content_type__app_label=app_label, codename=codename).exists(),
                            cap.perm)


class LeaverScanTests(VisaCase):
    def sponsor_is(self, status):
        Employee.objects.filter(pk=self.emp.pk).update(status=status)

    def test_hr_is_told_once_per_state_and_only_hr_of_that_company(self):
        self.make_visa()
        self.assertEqual(leavers.run_leaver_scan()["alerts"], 0)                       # the sponsor is working
        self.sponsor_is("on_notice")
        stats = leavers.run_leaver_scan()
        self.assertEqual((stats["checked"], stats["alerts"], stats["emails"]), (1, 1, 2))
        self.assertEqual(sorted(m.to[0] for m in mail.outbox), ["hr@example.com", "hr_b@example.com"])
        self.assertIn("is on notice", mail.outbox[0].subject)
        self.assertIn("Yusuf", mail.outbox[0].body)
        self.assertEqual(leavers.run_leaver_scan()["alerts"], 0)                       # not again
        mail.outbox.clear()
        self.sponsor_is("separated")
        self.assertEqual(leavers.run_leaver_scan()["alerts"], 1)                       # once more when they have left
        self.assertIn("has left", mail.outbox[0].subject)
        self.assertEqual(FamilyLeaverAlert.objects.filter(dependant=self.kid).count(), 2)

    def test_one_notice_per_sponsor_names_the_whole_family(self):
        layla = self.person("Layla")
        self.make_visa()
        self.make_visa(layla)
        self.sponsor_is("on_notice")
        stats = leavers.run_leaver_scan()
        self.assertEqual((stats["alerts"], stats["emails"]), (2, 2))
        self.assertTrue(all("Yusuf" in m.body and "Layla" in m.body for m in mail.outbox))

    def test_nobody_is_chased_who_has_nothing_to_cancel_or_is_already_being_cancelled(self):
        self.sponsor_is("on_notice")
        self.assertEqual(leavers.run_leaver_scan()["checked"], 0)                      # no visa yet
        self.make_visa()
        self.ask(Kind.CANCELLATION, reason="Sponsor is leaving")
        self.assertEqual(leavers.run_leaver_scan()["checked"], 0)                      # already in hand

    def test_a_family_member_no_longer_sponsored_is_left_alone(self):
        self.make_visa()
        family.deactivate_dependant(self.kid)
        self.sponsor_is("separated")
        self.assertEqual(leavers.run_leaver_scan()["checked"], 0)

    def test_the_cases_are_limited_to_the_companies_a_person_may_see(self):
        self.make_visa()
        self.sponsor_is("on_notice")
        self.assertEqual((leavers.leaver_cases(self.hr).count(), leavers.leaver_cases(self.other_hr).count()), (1, 0))

    def test_nobody_with_an_email_still_logs_the_alert(self):
        self.make_visa()
        self.sponsor_is("on_notice")
        User.objects.filter(groups__name="HR").update(email="")
        stats = leavers.run_leaver_scan()
        self.assertEqual((stats["alerts"], stats["emails"], len(mail.outbox)), (1, 0, 0))
        self.assertEqual(FamilyLeaverAlert.objects.get().recipients, 0)

    def test_management_command(self):
        self.make_visa()
        self.sponsor_is("on_notice")
        out = StringIO()
        call_command("run_family_scan", stdout=out)
        self.assertIn("1 alert(s)", out.getvalue())

    def test_the_daily_job_is_scheduled_automatically(self):
        try:
            from config.celery import app
        except ModuleNotFoundError:
            self.skipTest("Celery is not installed")
        entry = app.conf.beat_schedule["dependants-daily-scan"]
        self.assertEqual(entry["task"], "apps.dependants.tasks.daily_scan")
        app.loader.import_default_modules()
        self.assertIn(entry["task"], app.tasks)
