from datetime import date
from decimal import Decimal

from django.urls import reverse

from apps.employees.models import Employee
from apps.payroll import runs
from apps.payroll.payrun import PayrollRun
from apps.payroll.test_labor_runs import FIRST, LAST, WORKING, LaborCase
from apps.web.tests.test_payroll_runs import RunPageCase

D = Decimal


class LaborPageCase(RunPageCase, LaborCase):
    def make_run(self):
        ravi = self.worker("Ravi", basis="daily", rate="5", ot=False)
        saleh = self.worker("Saleh", basis="monthly", rate="300", ot=False, pay="cash")
        self.attend(ravi, WORKING[:10])
        self.attend(saleh)
        self.ravi, self.saleh = ravi, saleh
        return self.labor_run()


class StartingALaborRunTests(LaborPageCase):
    def test_the_form_offers_the_kind_and_a_labor_run_is_made_for_a_finished_month(self):
        form = self.client.get(self.url("payroll_run_create")).context["form"]
        self.assertEqual(dict(form.fields["kind"].choices), {"staff": "Staff", "labor": "Direct labor"})
        self.worker("Ravi", basis="daily", rate="5", ot=False)
        r = self.client.post(self.url("payroll_run_create"), {"company": self.co.pk, "kind": "labor", "month": 3, "year": 2026})
        run = PayrollRun.objects.get()
        self.assertEqual((run.kind, run.label), ("labor", "March 2026"))
        self.assertRedirects(r, self.url("payroll_run", run.pk), fetch_redirect_response=False)
        page = self.client.get(r.url)
        self.assertContains(page, "Direct labor March 2026")
        self.assertContains(page, "direct labor")

    def test_an_unfinished_month_is_a_form_error(self):
        today = date.today()
        r = self.client.post(self.url("payroll_run_create"),
                             {"company": self.co.pk, "kind": "labor", "month": today.month, "year": today.year})
        self.assertContains(r, "is not over yet")
        self.assertFalse(PayrollRun.objects.exists())

    def test_the_list_says_which_kind_each_run_is(self):
        self.make_run()
        self.new_run()
        page = self.client.get(self.url("payroll_runs"))
        self.assertContains(page, "Direct labor")
        self.assertContains(page, ">Staff<")
        self.assertEqual(len(page.context["rows"]), 2)


class LaborRunPageTests(LaborPageCase):
    def test_the_run_page_shows_the_workers_and_their_pay_but_not_the_staff(self):
        run = self.make_run()
        page = self.client.get(self.url("payroll_run", run.pk))
        for text in ("Payroll March 2026", "direct labor", "Ravi", "Saleh", "50.000", "300.000"):
            self.assertContains(page, text)
        self.assertEqual({l.employee_name for l in page.context["lines"]}, {"Ravi", "Saleh"})   # no staff in a labor run                                      # the staff are not in a labor run
        self.assertContains(page, "Contractor-supplied labor is paid through the contractor statement")
        self.assertEqual(page.context["totals"]["people"], 2)

    def test_problems_are_written_on_the_line_in_words(self):
        ravi = self.worker("Ravi", basis="daily", rate="5")                       # overtime yes, so hours matter
        self.attend(ravi, WORKING[:3])
        run = self.labor_run()
        page = self.client.get(self.url("payroll_run", run.pk))
        self.assertContains(page, "table-danger")
        self.assertContains(page, "no hours, and this worker&#x27;s pay depends on hours")
        self.assertContains(page, "no attendance entry")
        self.assertContains(page, "a problem to fix")

    def test_workers_without_a_labor_profile_are_named_on_the_page(self):
        run = self.make_run()
        Employee.objects.create(company=self.co, employee_no="L-900", first_name="Orphan", worker_type="labor",
                                joining_date=date(2020, 1, 1))
        page = self.client.get(self.url("payroll_run", run.pk))
        self.assertContains(page, "no labor profile")
        self.assertContains(page, "L-900 Orphan")

    def test_the_line_page_explains_the_labor_pay(self):
        run = self.make_run()
        line = run.lines.get(employee=self.saleh)
        page = self.client.get(self.url("payroll_line", run.pk, line.pk))
        for text in ("What the labor records say", "Pay basis", "monthly", "Gross pay (wages and approved overtime)",
                     "300.000"):
            self.assertContains(page, text)
        self.assertNotContains(page, "Salary in force this month")
        self.assertNotContains(page, "Absence and unpaid days")

    def test_recalculating_from_the_page_reads_the_labor_records_again(self):
        run = self.make_run()
        self.attend(self.ravi, WORKING[10:15])
        r = self.client.post(self.url("payroll_run_recalculate", run.pk), follow=True)
        self.assertContains(r, "Recalculated")
        self.assertEqual(run.lines.get(employee=self.ravi).gross, D("75.000"))      # 15 days at 5

    def test_the_downloads_include_the_labor_run(self):
        run = self.make_run()
        import io, zipfile
        register = self.client.get(self.url("payroll_run_export", run.pk, "register"))
        with zipfile.ZipFile(io.BytesIO(register.content)) as z:
            text = z.read("xl/worksheets/sheet1.xml").decode()
        for expected in ("Payroll register: March 2026", "Ravi", "Saleh", "Cash", "Bank transfer"):
            self.assertIn(expected, text)
        cash = self.client.get(self.url("payroll_run_export", run.pk, "cash"))
        with zipfile.ZipFile(io.BytesIO(cash.content)) as z:
            self.assertIn("Saleh", z.read("xl/worksheets/sheet1.xml").decode())

    def test_the_whole_flow_from_the_pages(self):
        run = self.make_run()
        self.attend(self.ravi)
        runs.calculate_run(run, self.hr)
        r = self.client.post(self.url("payroll_run_submit", run.pk), follow=True)
        self.assertContains(r, "Sent to Management")
        from apps.core import approvals
        from apps.payroll.test_runs import approvals_of
        approvals.decide(approvals_of(run).pk, self.boss, "approve")
        page = self.client.get(self.url("payroll_run", run.pk))
        self.assertContains(page, "Record bank transfers (1)")
        self.assertContains(page, "Record cash (1)")
