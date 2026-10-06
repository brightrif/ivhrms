import re
from contextlib import ExitStack
from datetime import timedelta
from types import SimpleNamespace
from unittest import mock

from django.contrib.staticfiles import finders
from django.test import SimpleTestCase
from django.urls import reverse

from apps.compliance import services
from apps.compliance.testing import ComplianceCase
from apps.web.calendar_hints import leave_calendar_hints
from apps.web.forms import date_input

ASSETS = ["web/vendor/air-datepicker/air-datepicker.js", "web/vendor/air-datepicker/air-datepicker.css",
          "web/datepickers.js", "web/datepicker-theme.css"]


class DateInputTests(SimpleTestCase):
    def test_without_rules_it_is_still_a_plain_native_date_field(self):
        widget = date_input()
        self.assertEqual((widget.input_type, widget.attrs), ("date", {}))
        self.assertEqual(widget.format, "%Y-%m-%d")          # the server always receives ISO dates

    def test_rules_become_data_attributes_for_the_picker(self):
        attrs = date_input(max="today", min_from="id_issue_date", view="years").attrs
        self.assertEqual((attrs["data-max"], attrs["data-min-from"], attrs["data-view"]),
                         ("today", "id_issue_date", "years"))


class LeaveCalendarHintTests(SimpleTestCase):
    def hints(self, *, shift=None, holidays=None):
        employee = SimpleNamespace(company_id=7)
        with ExitStack() as stack:
            stack.enter_context(mock.patch("apps.scheduling.services.shift_on", return_value=shift))
            stack.enter_context(mock.patch("apps.scheduling.services.default_weekly_off", return_value=[4]))
            lookup = stack.enter_context(mock.patch("apps.scheduling.services.holidays_between",
                                                    return_value=holidays or {}))
            return leave_calendar_hints(employee), lookup

    def test_friday_is_the_default_weekly_off_in_javascript_numbering(self):
        hints, _ = self.hints()
        self.assertEqual(hints["data-off-days"], "5")        # Python Friday=4 -> JavaScript Friday=5

    def test_the_employees_own_shift_decides_the_off_days(self):
        self.assertEqual(self.hints(shift=SimpleNamespace(weekly_off_days=[4, 5]))[0]["data-off-days"], "5,6")
        self.assertEqual(self.hints(shift=SimpleNamespace(weekly_off_days=[6]))[0]["data-off-days"], "0")   # Sunday

    def test_holidays_are_passed_as_json_for_a_window_around_today(self):
        from datetime import date
        day = date(2026, 12, 16)
        hints, lookup = self.hints(holidays={day: SimpleNamespace(name="National Day")})
        self.assertEqual(hints["data-holidays"], '{"2026-12-16": "National Day"}')
        company, start, end = lookup.call_args.args
        self.assertEqual(company, 7)
        self.assertGreaterEqual((end - start).days, 450)


class PickerWiringTests(ComplianceCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.hr)

    def html(self, name, *args):
        return self.client.get(reverse(f"web:{name}", args=args)).content.decode()

    def tag(self, html, field_id):
        return re.search(rf'<input[^>]*id="{field_id}"[^>]*>', html).group(0)

    def test_the_picker_files_exist(self):
        for path in ASSETS:
            self.assertIsNotNone(finders.find(path), f"{path} is missing: unzip the date picker files")

    def test_every_page_loads_the_picker_in_the_right_order(self):
        html = self.html("compliance_create")
        for path in ASSETS:
            self.assertIn(path, html, f"base.html does not load {path}")
        self.assertLess(html.index("air-datepicker.js"), html.index("datepickers.js"))      # library first
        self.assertLess(html.index("air-datepicker.css"), html.index("datepicker-theme.css"))   # theme over it

    def test_the_picker_spells_out_english_instead_of_trusting_the_librarys_default(self):
        script = open(finders.find("web/datepickers.js"), encoding="utf-8").read()
        self.assertIn("locale: LOCALE", script)           # the library's own default language is Russian
        self.assertIn('"January"', script)

    def test_the_today_button_selects_today_instead_of_only_scrolling_to_it(self):
        script = open(finders.find("web/datepickers.js"), encoding="utf-8").read()
        self.assertIn("buttons: [TODAY_BUTTON", script)      # the library's own "today" preset never selects anything
        self.assertNotIn('buttons: ["today"', script)

    def test_typed_text_is_only_reconciled_when_the_person_really_typed(self):
        # Without this guard a stale box value undoes a calendar pick or the Today button on a filled-in date.
        script = open(finders.find("web/datepickers.js"), encoding="utf-8").read()
        self.assertIn("if (!typed) { return; }", script)
        self.assertIn('input.addEventListener("input"', script)

    def test_compliance_dates_carry_the_rules_the_server_enforces(self):
        html = self.html("compliance_create")
        self.assertIn('data-max="today"', self.tag(html, "id_issue_date"))
        self.assertIn('data-min-from="id_issue_date"', self.tag(html, "id_expiry_date"))
        self.assertIn('type="date"', self.tag(html, "id_expiry_date"))      # still native until the script upgrades it

    def test_a_renewal_cannot_pick_the_current_expiry_or_earlier(self):
        doc = self.make_doc(30)
        html = self.html("compliance_renew", doc.pk)
        self.assertIn(f'data-min="{(doc.expiry_date + timedelta(days=1)).isoformat()}"', self.tag(html, "id_expiry_date"))

    def test_a_payment_cannot_be_dated_in_the_future(self):
        task = services.open_renewal(self.make_doc(30))
        self.assertIn('data-max="today"', self.tag(self.html("compliance_payment_create", task.pk), "id_paid_on"))
