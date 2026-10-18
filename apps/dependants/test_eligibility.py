from django.urls import reverse

from apps.audit.models import AuditEvent
from apps.employees.models import Employee
from apps.organization.models import Designation

from . import eligibility
from . import visa_services as visa
from .models import Dependant
from .sponsor import FamilyVisaException
from .testing import VisaCase
from .visa import FamilyVisaRequest

Kind = FamilyVisaRequest.Kind


class SponsorCase(VisaCase):
    """VisaCase allows the test sponsors so the older tests keep working; these tests start from the real default."""

    def setUp(self):
        super().setUp()
        for sponsor in self.sponsors():
            eligibility.set_exception(sponsor, "default", "")

    def sponsors(self):
        return [e for e in (self.emp, getattr(self, "emp2", None)) if e is not None]

    def sponsor(self):
        return Employee.objects.select_related("designation").get(pk=self.emp.pk)

    def fresh_kid(self):
        return Dependant.objects.get(pk=self.kid.pk)

    def designation(self, ticked=True):
        d = Designation.objects.create(name="Manager", family_visa_default=ticked)
        Employee.objects.filter(pk=self.emp.pk).update(designation=d)
        return d


class SponsorRuleTests(SponsorCase):
    def test_nobody_is_allowed_until_someone_decides(self):
        status = eligibility.sponsor_status(self.sponsor())
        self.assertEqual((status.allowed, status.source), (False, "default"))
        self.assertEqual(visa.allowed_kinds(self.fresh_kid()), [])
        with self.assertRaisesMessage(visa.VisaError, "does not have family status yet"):
            self.ask()
        self.assertFalse(FamilyVisaRequest.objects.exists())

    def test_a_ticked_designation_allows_its_people(self):
        d = self.designation(ticked=False)
        self.assertFalse(eligibility.sponsor_status(self.sponsor()).allowed)
        Designation.objects.filter(pk=d.pk).update(family_visa_default=True)
        status = eligibility.sponsor_status(self.sponsor())
        self.assertEqual((status.allowed, status.source), (True, "designation"))
        self.assertEqual(visa.allowed_kinds(self.fresh_kid()), [Kind.NEW])
        self.assertEqual(self.ask().status, "pending")

    def test_the_exception_beats_the_designation(self):
        self.designation(ticked=True)
        eligibility.set_exception(self.emp, "not_allowed", "Contract ends soon")
        status = eligibility.sponsor_status(self.sponsor())
        self.assertEqual((status.allowed, status.source, status.reason), (False, "exception", "Contract ends soon"))
        with self.assertRaisesMessage(visa.VisaError, "Contract ends soon"):
            self.ask()
        eligibility.set_exception(self.emp, "default", "")                      # back to the designation's default
        self.assertTrue(eligibility.sponsor_status(self.sponsor()).allowed)

    def test_an_exception_can_allow_someone_the_designation_does_not(self):
        eligibility.set_exception(self.emp, "allowed", "Approved by the director")
        status = eligibility.sponsor_status(self.sponsor())
        self.assertEqual((status.allowed, status.source), (True, "exception"))
        self.assertEqual(self.ask().status, "pending")

    def test_allowed_and_not_allowed_need_a_reason(self):
        for mode in ("allowed", "not_allowed"):
            with self.assertRaisesMessage(eligibility.EligibilityError, "reason"):
                eligibility.set_exception(self.emp, mode, "   ")
        with self.assertRaises(eligibility.EligibilityError):
            eligibility.set_exception(self.emp, "nonsense", "x")
        self.assertEqual(eligibility.set_exception(self.emp, "default", "").mode, "default")   # nothing to change

    def test_a_renewal_never_asks(self):
        self.make_visa()
        self.assertEqual(visa.allowed_kinds(self.fresh_kid()), [Kind.RENEWAL, Kind.CANCELLATION])
        self.assertEqual(self.ask(Kind.RENEWAL).status, "pending")

    def test_a_cancellation_never_asks(self):
        self.make_visa()
        self.assertEqual(self.ask(Kind.CANCELLATION, reason="Going home").status, "pending")

    def test_changing_the_decision_leaves_requests_already_made(self):
        eligibility.set_exception(self.emp, "allowed", "Approved by the director")
        req = self.ask()
        eligibility.set_exception(self.emp, "not_allowed", "Changed our minds")
        req.refresh_from_db()
        self.assertEqual(req.status, "pending")

    def test_a_decision_is_audited_against_the_sponsor(self):
        eligibility.set_exception(self.emp, "allowed", "Approved by the director")
        eligibility.set_exception(self.emp, "not_allowed", "Changed our minds")
        self.assertTrue(AuditEvent.objects.filter(module="dependants", subject_employee_id=self.emp.pk,
                                                  company_id=self.co.pk).exists())
        self.assertEqual(FamilyVisaException.objects.get(employee=self.emp).reason, "Changed our minds")

    def test_hr_and_management_can_decide_but_finance_cannot(self):
        perm = "dependants.change_familyvisaexception"
        self.assertTrue(self.hr.has_perm(perm))
        self.assertTrue(self.boss.has_perm(perm))
        self.assertFalse(self.finance.has_perm(perm))


class SponsorPagesTests(SponsorCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.hr)

    def test_the_picker_waits_for_two_letters_and_shows_only_matches(self):
        url = reverse("web:employee_family_pick")
        self.assertContains(self.client.get(url + "?q=a"), "at least 2 letters")
        resp = self.client.get(url + "?q=" + self.emp.employee_no)
        self.assertContains(resp, self.emp.full_name)
        self.assertContains(resp, "No family status")

    def test_adding_without_an_employee_goes_to_the_picker(self):
        resp = self.client.get(reverse("web:employee_family_add"))
        self.assertRedirects(resp, reverse("web:employee_family_pick"))

    def test_adding_for_a_known_employee_has_no_employee_box(self):
        eligibility.set_exception(self.emp, "allowed", "Approved by the manager")
        url = reverse("web:employee_family_add") + f"?employee={self.emp.pk}"
        self.assertNotContains(self.client.get(url), 'name="employee"')
        resp = self.client.post(url, {"relationship": "child", "first_name": "Zed", "last_name": "",
                                      "name_ar": "", "gender": "", "date_of_birth": self.iso(-2000),
                                      "nationality": "", "notes": ""})
        dep = Dependant.objects.get(first_name="Zed")
        self.assertEqual(dep.employee_id, self.emp.pk)
        self.assertEqual(resp.status_code, 302)

    def test_family_details_need_family_status_first(self):
        self.assertFalse(eligibility.sponsor_status(self.sponsor()).allowed)
        url = reverse("web:employee_family_add") + f"?employee={self.emp.pk}"
        self.assertRedirects(self.client.get(url), reverse("web:employee_family_status_add", args=[self.emp.pk]))
        self.client.post(url, {"relationship": "spouse", "first_name": "Noor", "last_name": "", "name_ar": "",
                               "gender": "", "date_of_birth": "", "nationality": "", "notes": ""})
        self.assertFalse(Dependant.objects.filter(first_name="Noor").exists())

    def test_removing_family_status_through_the_page(self):
        eligibility.set_exception(self.emp, "allowed", "Approved by the manager")
        url = reverse("web:employee_family_visa_edit", args=[self.emp.pk])
        self.client.post(url, {"mode": "not_allowed", "reason": ""})                  # no reason: nothing changes
        self.assertTrue(eligibility.sponsor_status(self.sponsor()).allowed)
        resp = self.client.post(url, {"mode": "not_allowed", "reason": "Contract ended"})
        self.assertEqual(resp.status_code, 302)
        status = eligibility.sponsor_status(self.sponsor())
        self.assertEqual((status.allowed, status.reason), (False, "Contract ended"))

    def test_the_employee_page_has_the_family_status_card(self):
        from django.contrib.auth.models import Permission
        self.hr.user_permissions.add(
            Permission.objects.get(content_type__app_label="employees", codename="view_employee"))
        resp = self.client.get(reverse("web:employee_detail", args=[self.emp.pk]))
        self.assertContains(resp, "Request family status")
        self.assertContains(resp, "No family status")
