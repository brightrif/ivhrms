import csv
import io
from decimal import Decimal

from django.urls import reverse

from apps.accounts.models import User
from apps.airtickets.reportcase import ReportCase


def rows_of(response):
    """The CSV a report sent back, as a list of rows (the first one is the header)."""
    assert response["Content-Type"].startswith("text/csv"), response["Content-Type"]
    text = response.content.decode("utf-8")
    assert text.startswith("\ufeff")                                # Excel needs this to read accents
    return list(csv.reader(io.StringIO(text[1:])))


class ReportPageCase(ReportCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.hr)

    def url(self, name, **query):
        base = reverse(f"web:{name}")
        return base + ("?" + "&".join(f"{k}={v}" for k, v in query.items()) if query else "")


class AccessTests(ReportPageCase):
    def test_who_can_open_the_reports(self):
        pages = ["employee_tickets_reports", "employee_tickets_report_eligible", "employee_tickets_report_used",
                 "employee_tickets_report_pending", "employee_tickets_report_expenses"]
        expected = {self.hr: 200, self.finance: 200, self.boss: 200, self.nobody: 403, self.other_hr: 200}
        self.client.logout()
        for name in pages:
            self.assertEqual(self.client.get(self.url(name)).status_code, 302, name)
        for user, status in expected.items():
            self.client.force_login(user)
            for name in pages:
                self.assertEqual(self.client.get(self.url(name)).status_code, status, f"{user.username}: {name}")
                self.assertEqual(self.client.get(self.url(name, csv=1)).status_code, status, f"{user.username}: {name} csv")

    def test_another_companys_figures_are_never_shown_or_downloaded(self):
        self.standard_year()
        self.client.force_login(self.other_hr)
        for name in ("employee_tickets_report_used", "employee_tickets_report_expenses",
                     "employee_tickets_report_eligible"):
            self.assertNotContains(self.client.get(self.url(name)), "Ali Test")
        self.assertEqual(len(rows_of(self.client.get(self.url("employee_tickets_report_used", csv=1)))), 1)  # header only


class IndexTests(ReportPageCase):
    def test_the_front_page_shows_the_headline_numbers(self):
        self.standard_year()
        r = self.client.get(self.url("employee_tickets_reports"))
        self.assertContains(r, "300.500")                                        # this year's spend
        self.assertEqual((r.context["eligible_count"], r.context["pending_count"], r.context["used_count"]),
                         (1, 0, 3))
        for name in ("eligible", "used", "pending", "expenses"):
            self.assertContains(r, reverse(f"web:employee_tickets_report_{name}"))


class UsedTests(ReportPageCase):
    def test_the_page_and_its_filters(self):
        self.standard_year()
        url = "employee_tickets_report_used"
        r = self.client.get(self.url(url))
        self.assertEqual(len(r.context["rows"]), 3)                              # this year by default
        self.assertContains(r, "300.500")
        self.assertEqual(len(self.client.get(self.url(url, year="all")).context["rows"]), 4)
        self.assertEqual(len(self.client.get(self.url(url, year=self.year - 1)).context["rows"]), 1)
        r = self.client.get(self.url(url, nationality="Bahraini"))
        self.assertEqual([e.employee for e in r.context["rows"]], [self.emp2])
        r = self.client.get(self.url(url, department=self.ops.pk))
        self.assertEqual({e.employee for e in r.context["rows"]}, {self.emp})

    def test_the_download_has_the_same_rows_and_keeps_the_filters(self):
        self.standard_year()
        r = self.client.get(self.url("employee_tickets_report_used", year=self.year, nationality="Bahraini", csv=1))
        self.assertIn("air-tickets-used", r["Content-Disposition"])
        rows = rows_of(r)
        self.assertEqual(rows[0][:4], ["Taken on", "Employee no", "Name", "Company"])
        self.assertEqual(len(rows), 2)
        self.assertEqual((rows[1][2], rows[1][4], rows[1][6], rows[1][8]), ("Sara Test", "Bahraini", "Paid as allowance", "100.500"))
        page = self.client.get(self.url("employee_tickets_report_used", year=self.year, nationality="Bahraini"))
        self.assertContains(page, "nationality=Bahraini&amp;csv=1")               # the button keeps the filters

    def test_text_that_looks_like_a_formula_is_defused_in_the_download(self):
        self.taken(self.emp, self.today.replace(month=1, day=1), Decimal("10"), destination="=HYPERLINK(1)")
        rows = rows_of(self.client.get(self.url("employee_tickets_report_used", csv=1)))
        self.assertIn("'=HYPERLINK(1)", [cell for row in rows for cell in row])


class ExpenseTests(ReportPageCase):
    def test_the_page_groups_and_totals(self):
        self.standard_year()
        url = "employee_tickets_report_expenses"
        r = self.client.get(self.url(url))
        self.assertEqual([x["label"] for x in r.context["table"]], ["Indian", "Bahraini"])
        self.assertContains(r, "200.000")
        self.assertContains(r, "300.500")
        r = self.client.get(self.url(url, by="department"))
        self.assertEqual([x["label"] for x in r.context["table"]], ["Operations", "No department"])
        self.assertContains(r, "No department")
        self.assertEqual(self.client.get(self.url(url, by="nonsense")).context["by"], "nationality")
        self.assertEqual(len(self.client.get(self.url(url, year="all", by="month")).context["table"]), 2)   # two years

    def test_a_ticket_without_a_cost_is_pointed_out(self):
        self.standard_year()
        r = self.client.get(self.url("employee_tickets_report_expenses"))
        self.assertEqual(r.context["grand"]["no_cost"], 1)
        self.assertContains(r, "No cost")

    def test_the_download_ends_with_a_total_row(self):
        self.standard_year()
        rows = rows_of(self.client.get(self.url("employee_tickets_report_expenses", by="department", csv=1)))
        self.assertEqual(rows[0][0], "Department")
        self.assertEqual([r[0] for r in rows[1:]], ["Operations", "No department", "All"])
        self.assertEqual(rows[-1][5], "300.500")


class EligibleAndPendingTests(ReportPageCase):
    def test_eligible(self):
        r = self.client.get(self.url("employee_tickets_report_eligible"))
        self.assertContains(r, "Ali Test")
        self.assertContains(r, "Eligible now")
        rows = rows_of(self.client.get(self.url("employee_tickets_report_eligible", csv=1)))
        self.assertEqual((rows[0][1], rows[1][1], rows[1][3], rows[1][-1]), ("Name", "Ali Test", "Indian", "No"))
        self.ask()
        self.assertEqual(rows_of(self.client.get(self.url("employee_tickets_report_eligible", csv=1)))[1][-1], "Yes")
        self.assertEqual(self.client.get(self.url("employee_tickets_report_eligible", within=30)).status_code, 200)

    def test_pending(self):
        self.assertContains(self.client.get(self.url("employee_tickets_report_pending")), "No requests are waiting")
        self.ask()
        r = self.client.get(self.url("employee_tickets_report_pending"))
        self.assertContains(r, "Ali Test")
        self.assertContains(r, "Approval: Reporting manager")
        rows = rows_of(self.client.get(self.url("employee_tickets_report_pending", csv=1)))
        self.assertEqual((rows[0][3], rows[1][3], rows[1][-2]), ("Name", "Ali Test", "Approval: Reporting manager"))


class SettingsAndMenuTests(ReportPageCase):
    def test_the_settings_tab_exists_and_the_menu_has_reports(self):
        url = reverse("web:settings_module", args=["airtickets"])
        self.assertEqual(self.client.get(url).status_code, 403)                  # HR has no settings right yet
        root = User.objects.create_user("root", is_superuser=True)
        self.client.force_login(root)
        r = self.client.get(url)
        self.assertContains(r, "Air Tickets")
        self.assertContains(r, "Remind this many days")
        self.client.force_login(self.hr)
        self.assertContains(self.client.get(reverse("web:employee_tickets")), reverse("web:employee_tickets_reports"))
