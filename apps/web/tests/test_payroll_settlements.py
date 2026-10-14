from datetime import date
from decimal import Decimal

from django.contrib.auth.models import Permission

from apps.audit.models import AuditEvent
from apps.core import approvals
from apps.employees.models import Employee
from apps.leave.models import LeaveLedger, LeaveType
from apps.payroll import settlements
from apps.payroll.finalsettlement import FinalSettlement, SettlementItem
from apps.payroll.test_settlements import LWD, SettleCase, approval_of
from apps.web.tests.test_payroll_runs import grant

D = Decimal
CASE = ["view_finalsettlement", "add_finalsettlement", "submit_finalsettlement", "pay_finalsettlement"]


class SettlementPageCase(SettleCase):
    def setUp(self):
        super().setUp()
        grant(self.hr, "view_payrollrun", *CASE)
        self.viewer = self.user("viewer")
        grant(self.viewer, "view_finalsettlement")
        self.nobody = self.user("nobody")
        from apps.organization.models import CompanyAccess
        from django.contrib.auth import get_user_model
        self.outsider = get_user_model().objects.create_user("outsider")
        CompanyAccess.objects.create(user=self.outsider, company=self.other)
        grant(self.outsider, *CASE)
        self.client.force_login(self.hr)

    def url(self, name, *args):
        from django.urls import reverse
        return reverse(f"web:{name}", args=args)

    def data(self, **over):
        data = {"employee": self.ali.pk, "reason": "resignation", "last_working_day": "2026-03-31",
                "notice_date": "2026-03-01", "notice_required_days": "30", "notice_treatment": "auto",
                "leave_type": self.annual.pk, "gratuity_decision": "", "gratuity_note": ""}
        data.update(over)
        return data

    def approved(self):
        case = self.settle()
        settlements.submit_settlement(case, self.hr)
        approvals.decide(approval_of(case).pk, self.boss, "approve")
        case.refresh_from_db()
        return case


class AccessTests(SettlementPageCase):
    def test_every_page_asks_for_a_sign_in_and_the_right(self):
        case = self.approved()
        pages = [self.url("payroll_settlements"), self.url("payroll_settlement_create"), self.url("payroll_settlement", case.pk),
                 self.url("payroll_settlement_statement", case.pk), self.url("payroll_settlement_certificate", case.pk),
                 self.url("payroll_settlement_pay", case.pk)]
        self.client.logout()
        for page in pages:
            self.assertEqual(self.client.get(page).status_code, 302, page)
        self.client.force_login(self.nobody)
        for page in pages:
            self.assertEqual(self.client.get(page).status_code, 403, page)
        self.client.force_login(self.outsider)
        for page in pages[2:]:
            self.assertEqual(self.client.get(page).status_code, 404, page)

    def test_a_viewer_may_look_but_not_act(self):
        case = self.settle()
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.get(self.url("payroll_settlements")).status_code, 200)
        page = self.client.get(self.url("payroll_settlement", case.pk))
        self.assertEqual(page.status_code, 200)
        for button in ("Recalculate", "Send for approval", "Add a line", "/edit/"):
            self.assertNotContains(page, button)
        for name in ("payroll_settlement_create", "payroll_settlement_edit", "payroll_settlement_item_add"):
            args = () if name.endswith("create") else (case.pk,)
            self.assertEqual(self.client.get(self.url(name, *args)).status_code, 403, name)
        for name in ("payroll_settlement_recalculate", "payroll_settlement_submit", "payroll_settlement_cancel",
                     "payroll_settlement_charges"):
            self.assertEqual(self.client.post(self.url(name, case.pk)).status_code, 403, name)
        self.assertEqual(FinalSettlement.objects.get().status, "draft")

    def test_preparing_sending_and_paying_are_separate_rights(self):
        case = self.settle()
        preparer = self.user("preparer")
        grant(preparer, "view_finalsettlement", "add_finalsettlement")
        self.client.force_login(preparer)
        self.assertEqual(self.client.post(self.url("payroll_settlement_recalculate", case.pk)).status_code, 302)
        self.assertEqual(self.client.post(self.url("payroll_settlement_submit", case.pk)).status_code, 403)
        self.assertEqual(self.client.get(self.url("payroll_settlement_pay", case.pk)).status_code, 403)

    def test_actions_are_posts_only(self):
        case = self.settle()
        for name in ("payroll_settlement_recalculate", "payroll_settlement_submit", "payroll_settlement_withdraw",
                     "payroll_settlement_cancel", "payroll_settlement_charges"):
            self.assertEqual(self.client.get(self.url(name, case.pk)).status_code, 405, name)


class CreatingTests(SettlementPageCase):
    def test_the_form_offers_the_staff_who_have_no_settlement_and_creates_the_case(self):
        form = self.client.get(self.url("payroll_settlement_create")).context["form"]
        self.assertEqual(set(form.fields["employee"].queryset.values_list("pk", flat=True)), {self.ali.pk, self.sara.pk, self.hr_emp.pk})
        r = self.client.post(self.url("payroll_settlement_create"), self.data())
        case = FinalSettlement.objects.get()
        self.assertRedirects(r, self.url("payroll_settlement", case.pk), fetch_redirect_response=False)
        page = self.client.get(r.url)
        for text in ("Ali", "Resignation", "31 Mar 2026", "Salary 01 Mar to 31 Mar 2026", "Annual leave: 27.5 days at 30.000",
                     "Gratuity for service before 1 March 2024", "4125.000", "Clearance checklist", "How it was worked out"):
            self.assertContains(page, text)
        self.assertContains(page, "The SIO pays the employee about")
        form = self.client.get(self.url("payroll_settlement_create")).context["form"]
        self.assertNotIn(self.ali.pk, set(form.fields["employee"].queryset.values_list("pk", flat=True)))

    def test_a_broken_rule_is_a_form_error_and_nothing_is_saved(self):
        r = self.client.post(self.url("payroll_settlement_create"), self.data(last_working_day="2019-01-01"))
        self.assertContains(r, "before the joining date")
        r = self.client.post(self.url("payroll_settlement_create"), self.data(gratuity_decision="no"))
        self.assertContains(r, "Say why no gratuity is payable")
        self.assertFalse(FinalSettlement.objects.exists())

    def test_the_decision_needed_for_a_dismissal_is_shown_in_red(self):
        r = self.client.post(self.url("payroll_settlement_create"), self.data(reason="summary", notice_date=""))
        page = self.client.get(r.url)
        self.assertContains(page, "Decide whether gratuity is payable")
        self.assertContains(page, "alert-danger")

    def test_changing_a_draft_recalculates_it(self):
        case = self.settle()
        r = self.client.post(self.url("payroll_settlement_edit", case.pk),
                             self.data(last_working_day="2026-03-20", notice_date="2026-02-18"))
        self.assertRedirects(r, self.url("payroll_settlement", case.pk), fetch_redirect_response=False)
        case.refresh_from_db()
        self.assertEqual(case.last_working_day, date(2026, 3, 20))
        self.assertEqual(case.items.get(kind="wages").label, "Salary 01 Mar to 20 Mar 2026")
        form = self.client.get(self.url("payroll_settlement_edit", case.pk)).context["form"]
        self.assertNotIn("employee", form.fields)

    def test_the_list_and_the_menu(self):
        self.settle()
        page = self.client.get(self.url("payroll_settlements"))
        self.assertContains(page, "Ali")
        self.assertContains(page, "4125.000")
        self.assertContains(page, "Final settlements")                         # the tab, for those with the right

    def test_every_form_places_every_field_it_has(self):
        case = self.settle()
        for page in (self.url("payroll_settlement_create"), self.url("payroll_settlement_edit", case.pk),
                     self.url("payroll_settlement_item_add", case.pk)):
            r = self.client.get(page)
            placed = [n for section in r.context["layout"] for row in section["rows"] for n in row]
            self.assertEqual(set(placed), set(r.context["form"].fields), page)
            self.assertEqual(len(placed), len(set(placed)), page)


class WorkingTheCaseTests(SettlementPageCase):
    def test_lines_charges_and_recalculation_from_the_page(self):
        case = self.settle()
        r = self.client.post(self.url("payroll_settlement_item_add", case.pk),
                             {"kind": "deduction", "label": "Loan balance", "amount": "250"})
        self.assertRedirects(r, self.url("payroll_settlement", case.pk), fetch_redirect_response=False)
        page = self.client.get(self.url("payroll_settlement", case.pk))
        self.assertContains(page, "Loan balance")
        self.assertContains(page, "3875.000")
        item = SettlementItem.objects.get(label="Loan balance")
        self.client.post(self.url("payroll_settlement_item_remove", case.pk, item.pk))
        self.assertEqual(FinalSettlement.objects.get().total, D("4125.000"))
        self.assertContains(self.client.post(self.url("payroll_settlement_recalculate", case.pk), follow=True), "Recalculated")
        self.assertContains(self.client.post(self.url("payroll_settlement_charges", case.pk), follow=True), "There is nothing to add")
        system_line = SettlementItem.objects.filter(is_system=True).first()
        r = self.client.post(self.url("payroll_settlement_item_remove", case.pk, system_line.pk), follow=True)
        self.assertContains(r, "worked out by the system")

    def test_problems_stop_the_send_and_are_shown_as_a_message(self):
        case = self.settle(reason="summary", notice_date=None)
        r = self.client.post(self.url("payroll_settlement_submit", case.pk), follow=True)
        self.assertContains(r, "problem to fix first")
        self.assertEqual(FinalSettlement.objects.get().status, "draft")

    def test_cancelling_a_draft(self):
        case = self.settle()
        r = self.client.post(self.url("payroll_settlement_cancel", case.pk))
        self.assertRedirects(r, self.url("payroll_settlements"), fetch_redirect_response=False)
        self.assertEqual(FinalSettlement.objects.get().status, "cancelled")


class ApprovalAndDocumentsTests(SettlementPageCase):
    def test_the_whole_flow_from_the_pages(self):
        case = self.settle()
        r = self.client.post(self.url("payroll_settlement_submit", case.pk), follow=True)
        self.assertContains(r, "Sent to Management")
        self.assertContains(r, "Waiting for Management to approve")
        self.assertContains(r, "Take back")
        self.assertNotContains(r, "Recalculate")
        approvals.decide(approval_of(case).pk, self.boss, "approve")
        page = self.client.get(self.url("payroll_settlement", case.pk))
        self.assertContains(page, "Approved")
        self.assertContains(page, "Record payment")
        self.assertContains(page, "Certificate")
        self.ali.refresh_from_db()
        self.assertEqual(self.ali.status, "separated")
        r = self.client.post(self.url("payroll_settlement_pay", case.pk), {"paid_on": "2026-04-02"})
        self.assertRedirects(r, self.url("payroll_settlement", case.pk), fetch_redirect_response=False)
        self.assertEqual(FinalSettlement.objects.get().status, "paid")
        self.assertContains(self.client.get(r.url), "Paid on 02 Apr 2026")

    def test_a_bad_payment_date_and_the_wrong_state(self):
        case = self.approved()
        r = self.client.post(self.url("payroll_settlement_pay", case.pk), {"paid_on": "2099-01-01"})
        self.assertEqual(r.status_code, 200)
        draft = FinalSettlement.objects.get(pk=case.pk)
        FinalSettlement.objects.filter(pk=case.pk).update(status="draft")
        r = self.client.get(self.url("payroll_settlement_pay", draft.pk), follow=True)
        self.assertContains(r, "once the settlement is approved")

    def test_the_statement_has_the_lines_the_totals_and_the_signatures_and_is_marked_draft_until_approved(self):
        case = self.settle()
        html = self.client.get(self.url("payroll_settlement_statement", case.pk)).content.decode()
        for text in ("Final settlement statement", "DRAFT, not approved", "Net amount payable to the employee", "4125.000",
                     "Gratuity for service before 1 March 2024", "Social Insurance Organisation", "Employee: name, signature, date",
                     "Print / Save as PDF"):
            self.assertIn(text, html)
        self.assertNotIn(VALID_IBAN_FULL, html)

    def test_the_certificate_only_after_approval_and_without_the_money_or_the_iban(self):
        case = self.settle()
        r = self.client.get(self.url("payroll_settlement_certificate", case.pk))
        self.assertRedirects(r, self.url("payroll_settlement", case.pk), fetch_redirect_response=False)
        self.assertContains(self.client.get(r.url), "issued once the settlement is approved")
        settlements.submit_settlement(case, self.hr)
        approvals.decide(approval_of(case).pk, self.boss, "approve")
        html = self.client.get(self.url("payroll_settlement_certificate", case.pk)).content.decode()
        for text in ("END OF SERVICE CERTIFICATE", "Ali", "S-1", "01 January 2020", "31 March 2026", "Resignation",
                     "BHD 900.000"):
            self.assertIn(text, html)
        self.assertNotIn("4125", html)
        self.assertNotIn(VALID_IBAN_FULL, html)

    def test_the_documents_are_audited_per_person(self):
        case = self.approved()
        self.client.get(self.url("payroll_settlement_statement", case.pk))
        self.client.get(self.url("payroll_settlement_certificate", case.pk))
        events = AuditEvent.objects.filter(action="download", module="payroll")
        self.assertEqual(events.count(), 2)
        self.assertEqual({e.subject_employee_id for e in events}, {self.ali.pk})
        self.assertNotIn("4125", str([e.changes for e in events]))


class AllowanceTypePageTests(SettlementPageCase):
    def test_the_social_allowance_tick_on_an_allowance_type(self):
        grant(self.hr, "view_allowancetype", "add_allowancetype")
        r = self.client.post(self.url("payroll_allowance_create"), {"company": self.co.pk, "name": "Social allowance",
                                                                  "counts_for_gratuity": "on"})
        self.assertEqual(r.status_code, 302)
        from apps.payroll.models import AllowanceType
        social = AllowanceType.objects.get(name="Social allowance")
        self.assertEqual((social.counts_for_gratuity, social.counts_for_sio), (True, False))
        page = self.client.get(self.url("payroll_allowances"))
        self.assertContains(page, "Social allowance")
        self.client.post(self.url("payroll_allowance_edit", social.pk), {"company": self.co.pk, "name": "Social allowance"})
        social.refresh_from_db()
        self.assertFalse(social.counts_for_gratuity)


VALID_IBAN_FULL = "BH67BMAG00001299123456"
