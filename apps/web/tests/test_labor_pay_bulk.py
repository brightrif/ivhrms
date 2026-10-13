"""Changing the pay switches of several ticked workers at once."""
from apps.employees.models import Employee

from .test_labor import LaborPageCase


class BulkSwitchTests(LaborPageCase):
    def post(self, profiles, **over):
        data = {"profiles": [p.pk for p in profiles], "effective_from": "2026-07-01",
                "overtime_eligible": "", "weekend_premium": "", "paid_holidays": ""}
        data.update(over)
        return self.client.post(self.url("labor_pay_bulk"), data)

    def test_the_list_offers_the_action(self):
        self.worker()
        self.assertContains(self.client.get(self.url("labor_list")), self.url("labor_pay_bulk"))

    def test_only_the_chosen_switches_change_and_the_history_is_kept(self):
        a, b = self.worker("Amin", rate="5"), self.worker("Basim", rate="6")
        r = self.post([a, b], overtime_eligible="0", paid_holidays="1")
        self.assertRedirects(r, self.url("labor_list"))
        for p, rate in ((a, "5.000"), (b, "6.000")):
            self.assertEqual(p.rates.count(), 2)
            now = p.rates.get(effective_to__isnull=True)
            self.assertEqual((now.overtime_eligible, now.paid_holidays, str(now.rate)), (False, True, rate))
            self.assertTrue(now.weekend_premium)                    # left as it was
            old = p.rates.get(effective_to__isnull=False)
            self.assertEqual(str(old.effective_to), "2026-06-30")
            self.assertTrue(old.overtime_eligible)                  # earlier months stay as they were

    def test_workers_who_already_have_the_choices_get_no_new_row(self):
        a = self.worker("Amin")
        r = self.post([a], overtime_eligible="1")                   # a new worker starts with overtime Yes
        self.assertRedirects(r, self.url("labor_list"))
        self.assertEqual(a.rates.count(), 1)

    def test_nobody_is_changed_if_one_cannot_be(self):
        a, b = self.worker("Amin"), self.worker("Basim")
        Employee.objects.filter(pk=b.employee_id).update(status="separated")
        r = self.post([a, b], overtime_eligible="0")
        self.assertContains(r, "Nobody was changed")
        self.assertEqual(a.rates.count(), 1)

    def test_at_least_one_switch_must_be_chosen(self):
        a = self.worker()
        self.assertContains(self.post([a]), "at least one switch")
        self.assertEqual(a.rates.count(), 1)

    def test_the_form_needs_ticked_workers(self):
        self.assertContains(self.client.get(self.url("labor_pay_bulk"), follow=True), "Tick at least one worker")

    def test_people_who_can_only_look_cannot_use_it(self):
        a = self.worker()
        for user in (self.finance, self.boss):
            self.client.force_login(user)
            self.assertEqual(self.client.get(self.url("labor_pay_bulk"), {"profiles": [a.pk]}).status_code, 403)
            self.assertEqual(self.post([a], overtime_eligible="0").status_code, 403)
        self.assertEqual(a.rates.count(), 1)
