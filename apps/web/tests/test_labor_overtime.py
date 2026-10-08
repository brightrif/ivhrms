from datetime import date
from decimal import Decimal

from django.contrib.auth.models import Permission

from apps.labor import overtime_services as ot
from apps.labor import sitesheet, timekeeping
from apps.labor.overtime import OvertimeClaim, OvertimePolicy

from .test_labor_sites import SitePageCase

SUN, MON, TUE, SAT = date(2026, 3, 1), date(2026, 3, 2), date(2026, 3, 3), date(2026, 3, 7)
D = Decimal


class OvertimePageCase(SitePageCase):
    def setUp(self):
        super().setUp()
        self.place(on=date(2026, 1, 5))
        self.site_key = f"{self.p1.pk}:{self.site1.pk}"
        sitesheet.save_sheet(self.hr, self.p1, self.site1, SUN, SAT, {}, "present")
        timekeeping.save_hours(self.hr, self.p1, self.site1, SUN, SAT,
                               {f"h_{self.p.employee_id}_20260302": "10", f"h_{self.p.employee_id}_20260303": "11"},
                               "standard")
        timekeeping.confirm_period(self.hr, self.p1, self.site1, SUN, SAT)
        self.manager = self.boss                                  # the Management group decides overtime

    def rules(self, start=date(2026, 1, 1)):
        return ot.set_policy(self.co, effective_from=start, working_day_multiplier=D("1.25"),
                             weekly_off_multiplier=D("1.5"), holiday_multiplier=D("1.5"), all_hours_on_days_off=True,
                             monthly_divisor=30)

    def page(self, **q):
        q = {"period": "week", "date": "2026-03-04", "site": self.site_key, **q}
        return self.client.get(self.url("labor_overtime"), q)

    def post(self, action, **extra):
        data = {"period": "week", "date": "2026-03-04", "site": self.site_key, "action": action, **extra}
        return self.client.post(self.url("labor_overtime"), data)

    def landing(self, response):
        return self.client.get(response.url)

    def prepared(self):
        self.rules()
        self.post("prepare")


class AccessTests(OvertimePageCase):
    def test_needs_login_and_permission(self):
        self.client.logout()
        for url in (self.url("labor_overtime"), self.url("labor_overtime_rules"), self.url("labor_overtime_rules_new")):
            self.assertEqual(self.client.get(url).status_code, 302, url)
        self.client.force_login(self.nobody)
        for url in (self.url("labor_overtime"), self.url("labor_overtime_rules"), self.url("labor_overtime_rules_new")):
            self.assertEqual(self.client.get(url).status_code, 403, url)
        self.assertEqual(self.post("prepare").status_code, 403)

    def test_hr_prepares_but_cannot_decide_or_void(self):
        self.rules()
        self.assertEqual(self.post("prepare").status_code, 302)
        self.assertEqual(OvertimeClaim.objects.count(), 2)
        pks = list(OvertimeClaim.objects.values_list("pk", flat=True))
        self.assertEqual(self.post("approve", claims=pks).status_code, 403)
        self.assertEqual(self.post("reject", claims=pks, note="x").status_code, 403)
        self.assertEqual(self.post("void").status_code, 403)
        self.assertEqual(OvertimeClaim.objects.filter(status="pending").count(), 2)
        page = self.page()
        self.assertContains(page, "Prepare claims")
        self.assertNotContains(page, "Approve ticked")

    def test_management_decides_but_does_not_prepare(self):
        self.prepared()
        self.client.force_login(self.manager)
        page = self.page()
        self.assertContains(page, "Approve ticked")
        self.assertNotContains(page, "Prepare claims")
        self.assertEqual(self.post("prepare").status_code, 403)
        self.assertEqual(self.post("void").status_code, 403)

    def test_finance_can_only_look(self):
        self.prepared()
        self.client.force_login(self.finance)
        page = self.page()
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, "Approve ticked")
        self.assertNotContains(page, "Prepare claims")
        for action in ("prepare", "approve", "reject", "void"):
            self.assertEqual(self.post(action).status_code, 403, action)

    def test_a_forged_action_is_refused(self):
        self.assertEqual(self.post("delete_everything").status_code, 403)

    def test_claims_of_other_companies_are_invisible_and_untouchable(self):
        self.prepared()
        self.outsider.user_permissions.add(*Permission.objects.filter(codename__in=["change_overtimeclaim"]))
        self.client.force_login(self.outsider)
        pks = list(OvertimeClaim.objects.values_list("pk", flat=True))
        page = self.page()
        self.assertContains(page, "No workers were allocated")
        self.post("approve", claims=pks)
        self.assertEqual(OvertimeClaim.objects.filter(status="pending").count(), 2)


class RulesPageTests(OvertimePageCase):
    def post_rules(self, **over):
        data = {"company": self.co.pk, "effective_from": "2026-01-01", "working_day_multiplier": "1.25",
                "weekly_off_multiplier": "1.50", "holiday_multiplier": "1.50", "all_hours_on_days_off": "on", "overtime_applies": "on",
                "monthly_divisor": "30"}
        data.update(over)
        return self.client.post(self.url("labor_overtime_rules_new"), data)

    def test_no_rules_until_someone_adds_them_and_the_page_says_so(self):
        page = self.client.get(self.url("labor_overtime_rules"))
        self.assertContains(page, "has no overtime decision yet")
        self.assertEqual(OvertimePolicy.objects.count(), 0)

    def test_hr_can_add_rules_and_a_second_set_closes_the_first(self):
        r = self.post_rules()
        self.assertRedirects(r, self.url("labor_overtime_rules"))
        self.post_rules(effective_from="2026-06-01", working_day_multiplier="1.5")
        first, second = OvertimePolicy.objects.order_by("effective_from")
        self.assertEqual(first.effective_to, date(2026, 5, 31))
        self.assertEqual((second.working_day_multiplier, second.effective_to), (D("1.50"), None))
        page = self.client.get(self.url("labor_overtime_rules"))
        self.assertContains(page, "&times;1.5")
        self.assertNotContains(page, "has no overtime decision yet")

    def test_bad_rules_are_refused_on_the_form(self):
        self.post_rules()
        self.assertContains(self.post_rules(effective_from="2026-01-01"), "must start after")
        self.assertContains(self.post_rules(effective_from="2026-07-01", holiday_multiplier="9"), "less than or equal to 5")
        self.assertContains(self.post_rules(effective_from="2026-07-01", monthly_divisor="40"), "less than or equal to 31")
        self.assertEqual(OvertimePolicy.objects.count(), 1)

    def test_every_field_of_the_form_is_placed_in_the_layout(self):
        r = self.client.get(self.url("labor_overtime_rules_new"))
        placed = {n for section in r.context["layout"] for row in section["rows"] for n in row}
        self.assertEqual(placed, set(r.context["form"].fields))

    def test_finance_and_management_can_see_the_rules_and_management_can_add_them(self):
        self.rules()
        self.client.force_login(self.finance)
        self.assertEqual(self.client.get(self.url("labor_overtime_rules")).status_code, 200)
        self.assertEqual(self.client.get(self.url("labor_overtime_rules_new")).status_code, 403)
        self.client.force_login(self.manager)
        self.assertEqual(self.client.get(self.url("labor_overtime_rules_new")).status_code, 200)


class ClaimsPageTests(OvertimePageCase):
    def test_without_rules_the_page_warns_and_preparing_is_refused(self):
        page = self.page()
        self.assertContains(page, "has no overtime decision yet")
        page = self.landing(self.post("prepare"))
        self.assertContains(page, "has no overtime decision")
        self.assertEqual(OvertimeClaim.objects.count(), 0)

    def test_prepare_shows_the_claims_with_their_working(self):
        self.rules()
        page = self.landing(self.post("prepare"))
        self.assertContains(page, "Prepared 2 overtime claims")
        c = OvertimeClaim.objects.get(date=MON)
        self.assertEqual((c.hours, c.amount), (D("2"), D("1.563")))
        page = self.page()
        self.assertContains(page, "1.563")
        self.assertContains(page, "0.6250")
        self.assertEqual(page.context["summary"]["pending"]["count"], 2)
        self.assertEqual(page.context["summary"]["pending"]["hours"], D("5"))

    def test_preparing_again_adds_nothing(self):
        self.prepared()
        page = self.landing(self.post("prepare"))
        self.assertContains(page, "No overtime to claim")
        self.assertEqual(OvertimeClaim.objects.count(), 2)

    def test_approve_and_reject_ticked_claims(self):
        self.prepared()
        self.client.force_login(self.manager)
        mon, tue = (OvertimeClaim.objects.get(date=d) for d in (MON, TUE))
        page = self.landing(self.post("approve", claims=[mon.pk]))
        self.assertContains(page, "Approved 1 claim")
        mon.refresh_from_db()
        self.assertEqual((mon.status, mon.decided_by), ("approved", self.manager))
        page = self.landing(self.post("reject", claims=[tue.pk]))
        self.assertContains(page, "Give a reason")
        self.landing(self.post("reject", claims=[tue.pk], note="Not authorised"))
        tue.refresh_from_db()
        self.assertEqual((tue.status, tue.decision_note), ("rejected", "Not authorised"))

    def test_deciding_with_nothing_ticked_and_deciding_twice(self):
        self.prepared()
        self.client.force_login(self.manager)
        self.assertContains(self.landing(self.post("approve")), "Tick the claims first")
        pks = list(OvertimeClaim.objects.values_list("pk", flat=True))
        self.post("approve", claims=pks)
        page = self.landing(self.post("reject", claims=pks, note="late change"))
        self.assertContains(page, "Rejected 0 claims")
        self.assertContains(page, "already decided were left alone")
        self.assertEqual(OvertimeClaim.objects.filter(status="approved").count(), 2)

    def test_decided_claims_have_no_tick_box(self):
        self.prepared()
        self.client.force_login(self.manager)
        self.post("approve", claims=[OvertimeClaim.objects.get(date=MON).pk])
        page = self.page().content.decode()
        self.assertEqual(page.count('name="claims"'), 1)                          # only the still-pending one

    def test_voiding_needs_the_delete_permission_and_then_the_timesheet_can_reopen(self):
        self.prepared()
        self.client.force_login(self.manager)
        self.post("approve", claims=list(OvertimeClaim.objects.values_list("pk", flat=True)))
        self.assertEqual(self.post("void").status_code, 403)
        self.manager.user_permissions.add(Permission.objects.get(codename="delete_overtimeclaim", content_type__app_label="labor"))
        self.manager = type(self.manager).objects.get(pk=self.manager.pk)
        self.client.force_login(self.manager)
        self.assertContains(self.page(), "Void all claims")
        self.assertContains(self.landing(self.post("void")), "Voided 2 overtime claims")
        self.assertEqual(OvertimeClaim.objects.count(), 0)
        self.assertEqual(timekeeping.reopen_period(self.hr, self.p1, self.site1, SUN, SAT), 6)

    def test_period_controls_and_tabs(self):
        self.assertEqual(self.client.get(self.url("labor_overtime")).status_code, 200)         # defaults
        junk = self.client.get(self.url("labor_overtime"), {"period": "x", "date": "no", "site": "q"})
        self.assertEqual(junk.status_code, 200)
        self.assertContains(self.client.get(self.url("labor_list")), self.url("labor_overtime"))


class NoOvertimeCompanyPageTests(OvertimePageCase):
    """Company one pays overtime; company two (the foreign project in this fixture) does not."""

    def setUp(self):
        super().setUp()
        self.hr.company_access.create(company=self.other_co)
        self.manager.company_access.create(company=self.other_co)
        self.b = self.worker("Imran", company=self.other_co)
        from apps.labor import deployment
        deployment.allocate(self.b.employee, project=self.foreign, location=self.site1, effective_from=date(2026, 1, 5))
        self.key_b = f"{self.foreign.pk}:{self.site1.pk}"
        sitesheet.save_sheet(self.hr, self.foreign, self.site1, SUN, SAT, {}, "present")
        timekeeping.save_hours(self.hr, self.foreign, self.site1, SUN, SAT,
                               {f"h_{self.b.employee_id}_20260302": "10"}, "standard")
        self.rules()                                              # company one: overtime applies
        ot.set_policy(self.other_co, effective_from=date(2026, 1, 1), overtime_applies=False,
                      working_day_multiplier=D("1.25"), weekly_off_multiplier=D("1.5"), holiday_multiplier=D("1.5"),
                      all_hours_on_days_off=True, monthly_divisor=30)

    def timesheet(self, site):
        return self.client.get(self.url("labor_timesheet"), {"period": "week", "date": "2026-03-04", "site": site})

    def overtime_page(self, site):
        return self.client.get(self.url("labor_overtime"), {"period": "week", "date": "2026-03-04", "site": site})

    def test_the_timesheet_of_the_no_overtime_company_shows_no_overtime_at_all(self):
        page = self.timesheet(self.key_b)
        self.assertEqual(page.context["sheet"].overtime_mode, "none")
        self.assertContains(page, "does not pay overtime")
        self.assertNotContains(page, "Overtime hours")
        self.assertNotContains(page, "border-warning")
        self.assertEqual(page.context["sheet"].overtime, D("0"))
        self.assertEqual(page.context["sheet"].hours, D("10") + 5 * 8)             # the extra hours are still recorded

    def test_the_other_company_is_unaffected(self):
        page = self.timesheet(self.site_key)
        self.assertEqual(page.context["sheet"].overtime_mode, "applies")
        self.assertContains(page, "Overtime hours")
        self.assertNotContains(page, "does not pay overtime")
        self.assertEqual(page.context["sheet"].overtime, D("5"))                    # 2 hours on Monday, 3 on Tuesday

    def test_the_overtime_tab_says_so_and_offers_nothing_to_do(self):
        page = self.overtime_page(self.key_b)
        self.assertContains(page, "does not pay overtime")
        self.assertNotContains(page, "Prepare claims")
        self.assertNotContains(page, "has no overtime decision yet")
        self.assertContains(self.overtime_page(self.site_key), "Prepare claims")

    def test_preparing_claims_there_creates_nothing(self):
        timekeeping.confirm_period(self.hr, self.foreign, self.site1, SUN, SAT)
        r = self.client.post(self.url("labor_overtime"), {"period": "week", "date": "2026-03-04", "site": self.key_b,
                                                          "action": "prepare"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(OvertimeClaim.objects.filter(employee=self.b.employee).count(), 0)

    def test_the_rules_page_shows_the_decision_and_the_form_records_it(self):
        page = self.client.get(self.url("labor_overtime_rules"))
        self.assertContains(page, "No overtime paid")
        self.assertContains(page, "&times;1.25")
        form = self.client.get(self.url("labor_overtime_rules_new")).context["form"]
        self.assertIn("overtime_applies", form.fields)

    def test_the_form_without_the_tick_records_no_overtime(self):
        self.client.force_login(self.manager)
        data = {"company": self.co.pk, "effective_from": "2026-06-01", "working_day_multiplier": "1.25",
                "weekly_off_multiplier": "1.50", "holiday_multiplier": "1.50", "monthly_divisor": "30"}
        self.assertEqual(self.client.post(self.url("labor_overtime_rules_new"), data).status_code, 302)
        latest = OvertimePolicy.objects.filter(company=self.co).order_by("-effective_from").first()
        self.assertFalse(latest.overtime_applies)

    def test_the_workers_page_says_the_company_pays_no_overtime(self):
        self.assertContains(self.client.get(self.url("labor_detail", self.b.pk)), "company pays no overtime")
        self.assertNotContains(self.client.get(self.url("labor_detail", self.p.pk)), "company pays no overtime")

    def test_a_company_with_no_decision_gets_a_banner_not_a_block(self):
        OvertimePolicy.objects.filter(company=self.other_co).delete()
        page = self.timesheet(self.key_b)
        self.assertEqual(page.context["sheet"].overtime_mode, "undecided")
        self.assertContains(page, "Nobody has said whether this company pays overtime")
        self.assertContains(page, "Save hours")                                    # still usable
