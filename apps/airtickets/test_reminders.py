from datetime import timedelta
from io import StringIO

from django.core import mail
from django.core.management import call_command

from apps.accounts.models import User
from apps.configuration import registry
from apps.configuration import services as settings_service

from . import reminders, services
from .reminderlog import TicketReminderLog
from .requestcase import RequestCase


class ReminderCase(RequestCase):
    def setUp(self):
        super().setUp()
        self.set_emp(self.emp, email="ali@example.com")

    def due_in(self, days, emp=None):
        """Move the joining date so the employee's next ticket falls due `days` from today."""
        emp = emp or self.emp
        target = self.today + timedelta(days=days)
        self.set_emp(emp, joining_date=services.add_months(target, -12))
        ent = services.sync_entitlement(emp)
        self.assertLessEqual(abs((ent.eligible_from - target).days), 3)
        return ent

    def recipients(self):
        return sorted(m.to[0] for m in mail.outbox)


class SettingsTests(ReminderCase):
    def test_the_starting_values_and_the_module(self):
        self.assertEqual(settings_service.get("airtickets.reminder_days"), 60)
        self.assertIs(settings_service.get("airtickets.remind_employee"), True)
        module = registry.get_module("airtickets")
        self.assertEqual(module.label, "Air Tickets")
        from django.contrib.auth.models import Permission
        for cap in module.capabilities:
            app_label, codename = cap.perm.split(".", 1)
            self.assertTrue(Permission.objects.filter(content_type__app_label=app_label, codename=codename).exists(),
                            cap.perm)


class ReminderTests(ReminderCase):
    def test_a_ticket_coming_up_is_reminded_once_to_hr_and_the_employee(self):
        self.due_in(30)
        stats = reminders.run_reminders(self.today)
        self.assertEqual((stats["checked"], stats["reminded"], stats["hr_emails"], stats["employee_emails"]),
                         (1, 1, 2, 1))
        self.assertEqual(self.recipients(), ["ali@example.com", "hr@example.com", "hr_b@example.com"])
        hr = next(m for m in mail.outbox if m.to == ["hr@example.com"])
        self.assertIn("1 employee with a ticket coming up", hr.subject)
        self.assertIn("Ali Test", hr.body)
        mine = next(m for m in mail.outbox if m.to == ["ali@example.com"])
        self.assertIn("Your air ticket is due on", mine.subject)
        self.assertIn("My air ticket", mine.body)
        mail.outbox.clear()
        self.assertEqual(reminders.run_reminders(self.today)["reminded"], 0)           # never twice
        self.assertEqual(mail.outbox, [])

    def test_a_ticket_that_has_just_fallen_due_is_reminded_once_more(self):
        self.due_in(30)
        reminders.run_reminders(self.today)
        mail.outbox.clear()
        self.due_in(-3)                                                                  # the date has now passed
        stats = reminders.run_reminders(self.today)
        self.assertEqual(stats["reminded"], 1)
        mine = next(m for m in mail.outbox if m.to == ["ali@example.com"])
        self.assertEqual(mine.subject, "Your air ticket is due now")
        self.assertEqual(TicketReminderLog.objects.filter(entitlement__employee=self.emp).count(), 2)

    def test_a_ticket_that_has_been_due_for_a_long_time_is_not_reminded(self):
        self.due_in(-30)                                                                 # long overdue
        self.assertEqual(reminders.run_reminders(self.today)["checked"], 0)
        self.assertEqual(mail.outbox, [])
        self.due_in(-10)                                                                 # recently due
        self.assertEqual(reminders.run_reminders(self.today)["reminded"], 1)

    def test_nobody_is_reminded_while_a_request_is_in_progress(self):
        self.due_in(30)
        self.ask(depart_on=self.today + timedelta(days=60), return_on=self.today + timedelta(days=90))
        self.assertEqual(reminders.run_reminders(self.today)["checked"], 0)

    def test_the_window_and_the_switches_come_from_settings(self):
        self.due_in(30)
        settings_service.set_value("airtickets.reminder_days", 10, self.co)
        self.assertEqual(reminders.run_reminders(self.today)["checked"], 0)              # not inside a 10 day window
        settings_service.set_value("airtickets.reminder_days", 0, self.co)
        self.assertEqual(reminders.run_reminders(self.today)["checked"], 0)              # 0 = off
        settings_service.set_value("airtickets.reminder_days", 45, self.co)
        settings_service.set_value("airtickets.remind_employee", False, self.co)
        stats = reminders.run_reminders(self.today)
        self.assertEqual((stats["reminded"], stats["employee_emails"], stats["hr_emails"]), (1, 0, 2))
        self.assertEqual(self.recipients(), ["hr@example.com", "hr_b@example.com"])

    def test_an_employee_without_an_email_is_just_left_out(self):
        self.set_emp(self.emp, email="")
        self.due_in(30)
        stats = reminders.run_reminders(self.today)
        self.assertEqual((stats["employee_emails"], stats["hr_emails"]), (0, 2))

    def test_hr_gets_one_summary_naming_everybody(self):
        self.policy(name="Bahrainis", nationalities=["Bahraini"])
        self.set_emp(self.emp2, email="sara@example.com")
        self.due_in(30)
        self.due_in(40, self.emp2)
        stats = reminders.run_reminders(self.today)
        self.assertEqual((stats["reminded"], stats["hr_emails"], stats["employee_emails"]), (2, 2, 2))
        hr_mails = [m for m in mail.outbox if m.to[0].startswith("hr")]
        self.assertEqual(len(hr_mails), 2)                                               # one each, not one per employee
        for m in hr_mails:
            self.assertIn("2 employees", m.subject)
            self.assertTrue("Ali Test" in m.body and "Sara Test" in m.body)

    def test_a_date_that_moves_is_reminded_about_again(self):
        self.due_in(30)
        reminders.run_reminders(self.today)
        mail.outbox.clear()
        self.due_in(40)
        self.assertEqual(reminders.run_reminders(self.today)["reminded"], 1)
        self.assertEqual(reminders.run_reminders(self.today)["reminded"], 0)

    def test_when_nobody_has_an_email_the_reminder_is_still_recorded(self):
        self.set_emp(self.emp, email="")
        User.objects.filter(groups__name="HR").update(email="")
        self.due_in(30)
        stats = reminders.run_reminders(self.today)
        self.assertEqual((stats["reminded"], len(mail.outbox)), (1, 0))
        self.assertEqual(TicketReminderLog.objects.get().recipients, 0)

    def test_leavers_and_labor_workers_are_not_reminded(self):
        self.due_in(30)
        self.set_emp(self.emp, status="separated")
        self.assertEqual(reminders.run_reminders(self.today)["checked"], 0)


class CommandAndJobTests(ReminderCase):
    def test_the_command(self):
        self.due_in(30)
        out = StringIO()
        call_command("run_ticket_reminders", stdout=out)
        self.assertIn("1 reminded", out.getvalue())

    def test_the_daily_job_syncs_then_reminds(self):
        try:
            from apps.airtickets.tasks import daily_sync
        except ModuleNotFoundError:
            self.skipTest("Celery is not installed")
        self.due_in(30)
        result = daily_sync()
        self.assertEqual(set(result), {"sync", "reminders"})
        self.assertEqual(result["reminders"]["reminded"], 1)
