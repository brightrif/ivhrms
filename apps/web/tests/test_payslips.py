from datetime import date
from decimal import Decimal

from apps.audit.models import AuditEvent
from apps.employees.models import Employee
from apps.payroll import runs, services
from apps.payroll.tests import VALID_IBAN
from apps.web.tests.test_payroll_labor import LaborPageCase
from apps.web.tests.test_payroll_runs import RunPageCase

D = Decimal


class PayslipCase(RunPageCase):
    def approved(self, *, absent=()):
        for e in (self.ali, self.sara, self.hr_emp):
            self.present(e, absent=absent if e == self.ali else ())
        run = runs.create_run(self.co, 2026, 3, user=self.hr)
        return self.approve(run)

    def slips(self, run):
        return self.client.get(self.url("payroll_payslips", run.pk))


class AccessAndStateTests(PayslipCase):
    def test_who_may_open_payslips_and_when(self):
        run = self.approved()
        line = run.lines.first()
        pages = [self.url("payroll_payslips", run.pk), self.url("payroll_payslip", run.pk, line.pk)]
        self.client.logout()
        for page in pages:
            self.assertEqual(self.client.get(page).status_code, 302, page)
        self.client.force_login(self.nobody)
        for page in pages:
            self.assertEqual(self.client.get(page).status_code, 403, page)
        self.client.force_login(self.outsider)
        for page in pages:
            self.assertEqual(self.client.get(page).status_code, 404, page)
        self.client.force_login(self.viewer)
        for page in pages:
            self.assertEqual(self.client.get(page).status_code, 200, page)

    def test_a_draft_or_waiting_run_issues_no_payslips(self):
        for emp in (self.ali, self.sara, self.hr_emp):
            self.present(emp)
        run = runs.create_run(self.co, 2026, 3, user=self.hr)
        line = run.lines.first()
        for page in (self.url("payroll_payslips", run.pk), self.url("payroll_payslip", run.pk, line.pk)):
            r = self.client.get(page)
            self.assertRedirects(r, self.url("payroll_run", run.pk), fetch_redirect_response=False)
        self.assertContains(self.client.get(self.url("payroll_run", run.pk)), "Send for approval")   # no payslips menu yet
        self.assertNotContains(self.client.get(self.url("payroll_run", run.pk)), "All payslips")
        self.assertNotContains(self.client.get(self.url("payroll_line", run.pk, line.pk)), "Payslip</a>")

    def test_a_line_of_another_run_is_not_found(self):
        run = self.approved()
        self.assertEqual(self.client.get(self.url("payroll_payslip", run.pk, 99999)).status_code, 404)

    def test_the_buttons_appear_once_the_run_is_approved(self):
        run = self.approved()
        line = run.lines.first()
        self.assertContains(self.client.get(self.url("payroll_run", run.pk)), "All payslips (print or save as PDF)")
        self.assertContains(self.client.get(self.url("payroll_line", run.pk, line.pk)), self.url("payroll_payslip", run.pk, line.pk))


class StaffPayslipTests(PayslipCase):
    def test_every_person_gets_a_slip_with_the_make_up_of_the_salary(self):
        run = self.approved()
        page = self.slips(run)
        self.assertEqual(page.content.decode().count('<section class="slip">'), 3)
        for text in ("IV One", "March 2026", "01 Mar 2026 to 31 Mar 2026", "Employee no.", "S-1", "Ali", "Sara",
                     "Basic salary", "1000.000", "Housing", "200.000", "Total earnings", "1200.000", "Net pay",
                     "Print / Save as PDF", "break-after: page"):
            self.assertContains(page, text)

    def test_deductions_include_the_employee_sio_share_only(self):
        page = self.slips(self.approved())
        html = page.content.decode()
        self.assertIn("Social insurance (SIO)", html)
        self.assertIn("50.000", html)                                     # Sara, 5% of 1000
        self.assertNotIn("employer", html.lower())                        # the company's share is a cost, not on the slip
        self.assertIn("1150.000", html)                                   # Sara's net

    def test_the_totals_add_up_on_the_slip(self):
        run = self.approved()
        line = run.lines.get(employee=self.sara)
        html = self.client.get(self.url("payroll_payslip", run.pk, line.pk)).content.decode()
        self.assertIn('Total earnings</td><td class="money">1200.000</td>', html)
        self.assertIn('Total deductions</td><td class="money">50.000</td>', html)       # the SIO share
        self.assertIn("1150.000 BHD", html)
        ali = run.lines.get(employee=self.ali)
        html = self.client.get(self.url("payroll_payslip", run.pk, ali.pk)).content.decode()
        self.assertIn('Total deductions</td><td class="money">0.000</td>', html)         # nothing taken from Ali

    def test_the_iban_is_masked(self):
        html = self.slips(self.approved()).content.decode()
        self.assertNotIn(VALID_IBAN, html)
        self.assertNotIn("BMAG", html)
        self.assertIn("BH67 \u2022\u2022\u2022\u2022 3456", html)
        self.assertIn("NBB", html)
        self.assertIn("Cash", html)                                       # the HR officer is paid in cash

    def test_absence_and_adjustments_are_on_the_right_side_of_the_slip(self):
        for emp in (self.ali, self.sara, self.hr_emp):
            self.present(emp, absent=(10,) if emp == self.ali else ())
        run = runs.create_run(self.co, 2026, 3, user=self.hr)
        runs.add_adjustment(run, self.ali, kind="addition", category="bonus", label="Eid bonus", amount=D("50"))
        runs.add_adjustment(run, self.ali, kind="deduction", category="advance", label="Salary advance", amount=D("100"))
        run = self.approve(run)
        line = run.lines.get(employee=self.ali)
        html = self.client.get(self.url("payroll_payslip", run.pk, line.pk)).content.decode()
        self.assertEqual(html.count('<section class="slip">'), 1)
        self.assertNotIn("Sara", html)
        self.assertLess(html.index("Eid bonus"), html.index("Total earnings"))
        self.assertGreater(html.index("Salary advance"), html.index("Deductions"))
        self.assertIn("Absence and unpaid days (1 day)", html)
        self.assertIn("30.000", html)
        self.assertIn("820.000", html)                                    # 900 + 50 - 30 - 100

    def test_a_part_month_shows_the_days_instead_of_the_components(self):
        joiner = self.staff("Joiner", "S-9", nationality="Indian", basic="620")
        Employee.objects.filter(pk=joiner.pk).update(joining_date=date(2026, 3, 16))
        from apps.payroll.models import SalaryRecord
        SalaryRecord.objects.filter(employee=joiner).update(effective_from=date(2026, 3, 16))
        for emp in (self.ali, self.sara, self.hr_emp, joiner):
            self.present(emp)
        run = self.approve(runs.create_run(self.co, 2026, 3, user=self.hr))
        line = run.lines.get(employee=joiner)
        html = self.client.get(self.url("payroll_payslip", run.pk, line.pk)).content.decode()
        self.assertIn("Salary 2026-03-16 to 2026-03-31 (16 of 31 days)", html)
        self.assertIn("320.000", html)                                    # 620 x 16/31

    def test_the_line_keeps_the_salary_make_up_for_the_slip(self):
        run = self.approved()
        segment = run.lines.get(employee=self.sara).breakdown["segments"][0]
        self.assertEqual(segment["parts"], {"basic": "1000.000", "allowances": [["Housing", "200.000"]]})

    def test_a_line_calculated_before_this_change_still_prints(self):
        run = self.approved()
        line = run.lines.get(employee=self.sara)
        for seg in line.breakdown["segments"]:
            seg.pop("parts")
        type(line).objects.filter(pk=line.pk).update(breakdown=line.breakdown)
        page = self.client.get(self.url("payroll_payslip", run.pk, line.pk))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Salary 2026-03-01 to 2026-03-31")

    def test_the_paid_date_shows_once_paid(self):
        run = self.approved()
        runs.mark_paid(run, list(run.lines.values_list("pk", flat=True)), date(2026, 4, 2))
        self.assertContains(self.slips(run), "Paid on 02 Apr 2026")

    def test_every_slip_viewed_is_audited_for_that_person(self):
        run = self.approved()
        self.slips(run)
        events = AuditEvent.objects.filter(action="download", module="payroll")
        self.assertEqual(events.count(), 3)
        self.assertEqual({e.subject_employee_id for e in events}, set(run.lines.values_list("employee_id", flat=True)))
        self.assertEqual({e.company_id for e in events}, {self.co.pk})
        self.assertNotIn("1200", str([e.changes for e in events]))


class LaborPayslipTests(LaborPageCase):
    def test_a_labor_slip_lists_the_wages_by_kind_and_the_overtime(self):
        from apps.payroll.test_labor_runs import MON2, WORKING
        ravi = self.worker("Ravi", basis="daily", rate="5")
        saleh = self.worker("Saleh", basis="monthly", rate="300", ot=False, pay="cash")
        self.attend(ravi)
        self.attend(saleh)
        self.full_month_hours(ravi, **{"2": "10"})
        self.claims()
        run = self.approve(self.labor_run())
        html = self.client.get(self.url("payroll_payslips", run.pk)).content.decode()
        self.assertEqual(html.count('<section class="slip">'), 2)
        for text in ("Ravi", "Saleh", "Daily wage", "135.000", "Monthly salary", "300.000",
                     "Overtime and premium (2 hours)", "1.563", "136.563", "Days worked / hours", "27 days"):
            self.assertIn(text, html)
        self.assertNotIn("Basic salary", html)
        self.assertNotIn("Hana", html)                                    # no staff on a labor payslip run
