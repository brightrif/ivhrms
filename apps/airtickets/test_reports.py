from datetime import timedelta
from decimal import Decimal

from django.test import SimpleTestCase

from apps.core import approvals

from . import reports, services
from . import request_services as tickets
from .reportcase import ReportCase
from .requestcase import RequestCase


class DownloadTests(SimpleTestCase):
    def test_a_cell_that_looks_like_a_formula_is_defused(self):
        for text in ("=SUM(A1)", "+1", "-1", "@x"):
            self.assertEqual(reports._safe(text), "'" + text)
        for fine in ("Cochin (COK)", "", "Operations", 12):
            self.assertEqual(reports._safe(fine), fine)

    def test_the_file_opens_in_excel_with_accents(self):
        response = reports.csv_response("x.csv", ["Name"], [["Zoë"], [None]])
        body = b"".join(response.streaming_content if response.streaming else [response.content])
        self.assertTrue(body.startswith(b"\xef\xbb\xbf"))
        self.assertIn("Zoë".encode(), body)
        self.assertIn("attachment", response["Content-Disposition"])


class UsedTests(ReportCase):
    def test_the_years_and_the_filters(self):
        self.standard_year()
        self.assertEqual(reports.years(self.hr), [self.year, self.year - 1])
        self.assertEqual(len(reports.utilised(self.hr, year=self.year)), 3)
        self.assertEqual(len(reports.utilised(self.hr)), 4)
        sara = reports.utilised(self.hr, year=self.year, nationality="Bahraini")
        self.assertEqual([(e.employee, e.status) for e in sara], [(self.emp2, "paid")])
        ops = reports.utilised(self.hr, year=self.year, department=self.ops.pk)
        self.assertEqual({e.employee_id for e in ops}, {self.emp.pk})
        self.assertEqual(reports.utilised(self.hr, year=self.year, company=self.co.pk + 999), [])

    def test_the_labels_tidy_the_nationality_and_name_the_missing_department(self):
        self.standard_year()
        rows = {e.employee_id: e for e in reports.utilised(self.hr, year=self.year)}
        self.assertEqual((rows[self.emp2.pk].nationality_label, rows[self.emp2.pk].department_label),
                         ("Bahraini", "No department"))
        self.assertEqual(rows[self.emp.pk].department_label, "Operations")

    def test_only_the_companies_a_person_may_see(self):
        self.standard_year()
        self.assertEqual(reports.utilised(self.other_hr, year=self.year), [])
        self.assertEqual(reports.years(self.other_hr), [])


class ExpenseTests(ReportCase):
    def table(self, by):
        return reports.expenses(reports.utilised(self.hr, year=self.year), by)

    def test_by_nationality(self):
        self.standard_year()
        table, grand = self.table("nationality")
        self.assertEqual([(r["label"], r["taken"], r["allowance"], r["no_cost"], r["cost"]) for r in table],
                         [("Indian", 2, 0, 1, Decimal("200.000")), ("Bahraini", 0, 1, 0, Decimal("100.500"))])
        self.assertEqual((table[0]["average"], table[0]["share"], table[1]["share"]), (Decimal("200.000"), 67, 33))
        self.assertEqual((grand["count"], grand["taken"], grand["allowance"], grand["no_cost"], grand["cost"]),
                         (3, 2, 1, 1, Decimal("300.500")))

    def test_by_department_company_and_month(self):
        self.standard_year()
        table, _ = self.table("department")
        self.assertEqual([(r["label"], r["cost"]) for r in table],
                         [("Operations", Decimal("200.000")), ("No department", Decimal("100.500"))])
        table, _ = self.table("company")
        self.assertEqual([(r["label"], r["count"]) for r in table], [(self.co.name, 3)])
        table, _ = self.table("month")
        self.assertEqual([r["label"] for r in table], [f"{self.year}-01"])
        table, _ = self.table("nonsense")                                            # unknown grouping: nationality
        self.assertEqual(table[0]["label"], "Indian")

    def test_an_empty_period_has_no_rows_and_a_zero_total(self):
        table, grand = self.table("nationality")
        self.assertEqual((table, grand["cost"], grand["count"]), ([], Decimal("0"), 0))


class EligibleTests(RequestCase):
    def test_who_is_due_and_who_has_a_request_in_progress(self):
        rows = reports.eligible(self.hr, within_days=90)
        self.assertEqual([e.employee for e in rows], [self.emp])
        e = rows[0]
        self.assertTrue(e.days < 0 and e.nationality_label == "Indian" and not e.has_request and e.last_travel is None)
        self.ask()
        self.assertTrue(reports.eligible(self.hr)[0].has_request)

    def test_the_window_leavers_and_other_companies(self):
        self.set_emp(self.emp, joining_date=self.today - timedelta(days=365 - 60))      # due in about 60 days
        services.sync_entitlement(self.emp)
        self.assertEqual(reports.eligible(self.hr, within_days=30), [])
        self.assertEqual(len(reports.eligible(self.hr, within_days=90)), 1)
        self.assertEqual(reports.eligible(self.other_hr, within_days=365), [])
        self.set_emp(self.emp, status="separated")
        self.assertEqual(reports.eligible(self.hr, within_days=365), [])


class PendingTests(RequestCase):
    def test_what_each_request_is_waiting_for(self):
        req = self.ask()
        row = reports.pending(self.hr)[0]
        self.assertEqual((row.waiting_for, row.age_days), ("Approval: Reporting manager", 0))
        approvals.decide(self.approval_of(req).pk, self.boss, "approve")
        self.assertEqual(reports.pending(self.hr)[0].waiting_for, "Approval: HR")
        approvals.decide(self.approval_of(req).pk, self.hr_b, "approve")
        self.assertEqual(reports.pending(self.hr)[0].waiting_for, "HR: issue the tickets")
        req.refresh_from_db()
        tickets.issue(req, self.hr, issued_on=self.today,
                      tickets={t.pk: ("176-1", Decimal("100")) for t in req.travellers.all()})
        self.assertTrue(reports.pending(self.hr)[0].waiting_for.startswith("Travel on"))

    def test_an_allowance_waits_for_payment_and_finished_requests_are_not_listed(self):
        self.to_allowance()
        req = self.approve(self.ask())
        self.assertEqual(reports.pending(self.hr)[0].waiting_for, "HR: record the allowance")
        tickets.pay_allowance(req, paid_on=self.today)
        self.assertEqual(reports.pending(self.hr), [])
        self.assertEqual(reports.pending(self.other_hr), [])
