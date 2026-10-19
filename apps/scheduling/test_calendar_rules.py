from datetime import date, time

from django.test import TestCase

from apps.employees.models import Employee
from apps.organization.models import Company

from . import calendar_rules as rules
from . import services
from .models import Holiday, Shift
from .weekly_off import WeeklyOffRule

THU, FRI, SAT, SUN = date(2026, 10, 15), date(2026, 10, 16), date(2026, 10, 17), date(2026, 10, 18)


class CalendarCase(TestCase):
    def setUp(self):
        self.co = Company.objects.create(code="C1", name="Company One")
        self.emp = Employee.objects.create(company=self.co, employee_no="E1", first_name="Ali",
                                           joining_date=date(2020, 1, 1))

    def kinds(self, first=THU, last=SUN):
        return services.day_types(self.emp, first, last)


class WeeklyOffTests(CalendarCase):
    def test_friday_is_the_weekly_off_until_a_rule_says_otherwise(self):
        self.assertEqual([self.kinds()[d] for d in (THU, FRI, SAT, SUN)], ["working", "weekly_off", "working", "working"])

    def test_a_rule_changes_the_days_from_its_date_and_earlier_days_keep_theirs(self):
        rules.set_weekly_off(self.co, [4, 5], date(2026, 11, 1))
        self.assertEqual(self.kinds()[SAT], "working")                                  # October: Friday only
        later = services.day_types(self.emp, date(2026, 11, 6), date(2026, 11, 7))
        self.assertEqual(list(later.values()), ["weekly_off", "weekly_off"])             # November: Friday and Saturday

    def test_a_later_rule_closes_the_earlier_one(self):
        first = rules.set_weekly_off(self.co, [4, 5], date(2026, 11, 1))
        rules.set_weekly_off(self.co, [5, 6], date(2027, 1, 1))
        first.refresh_from_db()
        self.assertEqual(first.effective_to, date(2026, 12, 31))
        self.assertEqual(WeeklyOffRule.objects.filter(company=self.co, effective_to__isnull=True).count(), 1)
        self.assertEqual(rules.weekly_off_on(self.co.pk, date(2026, 12, 4)), [4, 5])
        self.assertEqual(rules.weekly_off_on(self.co.pk, date(2027, 1, 2)), [5, 6])

    def test_another_company_is_not_affected(self):
        other = Company.objects.create(code="C2", name="Company Two")
        rules.set_weekly_off(self.co, [5, 6], date(2026, 10, 1))
        self.assertEqual(rules.weekly_off_on(other.pk, SAT), [4])
        self.assertEqual(rules.weekly_off_on(self.co.pk, SAT), [5, 6])

    def test_a_shifts_own_days_still_win_for_the_people_on_it(self):
        rules.set_weekly_off(self.co, [5, 6], date(2026, 10, 1))
        shift = Shift.objects.create(company=self.co, code="N", name="Night", start_time=time(22), end_time=time(6),
                                     weekly_off_days=[2])                                # Wednesday
        services.assign_shift(self.emp, shift, date(2026, 10, 1))
        kinds = services.day_types(self.emp, date(2026, 10, 14), date(2026, 10, 18))
        self.assertEqual(kinds[date(2026, 10, 14)], "weekly_off")                        # the shift's day
        self.assertEqual(kinds[SAT], "working")                                          # not the company's

    def test_a_holiday_wins_over_everything(self):
        Holiday.objects.create(company=None, date=SAT, name="Test holiday")
        self.assertEqual(self.kinds()[SAT], "holiday")
        Holiday.objects.create(company=None, date=FRI, name="Friday holiday")
        self.assertEqual(self.kinds()[FRI], "holiday")

    def test_the_rule_must_be_sensible(self):
        for days, text in (([], "at least one day"), ([0, 1, 2, 3, 4, 5, 6], "at least one working day"),
                           ([9], "Monday to Sunday"), (["x"], "days of the week")):
            with self.assertRaisesMessage(rules.CalendarError, text):
                rules.set_weekly_off(self.co, days, date(2026, 11, 1))
        with self.assertRaisesMessage(rules.CalendarError, "already Friday"):
            rules.set_weekly_off(self.co, [4], date(2026, 11, 1))                       # that is the built-in default
        rules.set_weekly_off(self.co, [4, 5], date(2026, 11, 1))
        with self.assertRaisesMessage(rules.CalendarError, "must start after"):
            rules.set_weekly_off(self.co, [5], date(2026, 11, 1))
        with self.assertRaisesMessage(rules.CalendarError, "already Friday, Saturday"):
            rules.set_weekly_off(self.co, [5, 4], date(2026, 12, 1))

    def test_the_names_are_written_in_words(self):
        self.assertEqual(rules.weekday_names([5, 4]), "Friday, Saturday")


class HolidayTests(CalendarCase):
    def test_a_range_makes_one_row_per_day(self):
        made = rules.add_holidays(self.co, date(2026, 3, 20), date(2026, 3, 23), "Eid Al Fitr", "عيد")
        self.assertEqual([h.date.day for h in made], [20, 21, 22, 23])
        self.assertEqual(set(Holiday.objects.values_list("name_ar", flat=True)), {"عيد"})

    def test_a_one_day_holiday_needs_no_last_day(self):
        self.assertEqual(len(rules.add_holidays(self.co, date(2026, 12, 16), None, "National Day")), 1)

    def test_global_holidays_apply_to_every_company(self):
        other = Company.objects.create(code="C2", name="Company Two")
        rules.add_holidays(None, date(2026, 12, 16), None, "National Day")
        for company in (self.co, other):
            self.assertIn(date(2026, 12, 16), services.holidays_between(company.pk, date(2026, 12, 1), date(2026, 12, 31)))

    def test_nothing_is_half_added_when_one_day_is_taken(self):
        rules.add_holidays(self.co, date(2026, 3, 21), None, "Existing")
        with self.assertRaisesMessage(rules.CalendarError, "21 Mar 2026"):
            rules.add_holidays(self.co, date(2026, 3, 20), date(2026, 3, 23), "Eid")
        self.assertEqual(Holiday.objects.count(), 1)

    def test_the_rules_for_a_range(self):
        for args, text in (((self.co, date(2026, 3, 23), date(2026, 3, 20), "x"), "cannot be before"),
                           ((self.co, date(2026, 3, 1), date(2026, 5, 1), "x"), "at most 31 days"),
                           ((self.co, date(2026, 3, 1), None, "   "), "Give the holiday a name")):
            with self.assertRaisesMessage(rules.CalendarError, text):
                rules.add_holidays(*args)

    def test_switching_off_stops_it_counting_and_switching_on_restores_it(self):
        h = rules.add_holidays(self.co, SAT, None, "Test")[0]
        rules.set_holiday_active(h, False)
        self.assertEqual(self.kinds()[SAT], "working")
        rules.set_holiday_active(h, True)
        self.assertEqual(self.kinds()[SAT], "holiday")

    def test_renaming_keeps_the_date(self):
        h = rules.add_holidays(self.co, SAT, None, "Old")[0]
        rules.rename_holiday(h, "  New   name ", "اسم")
        h.refresh_from_db()
        self.assertEqual((h.name, h.name_ar, h.date), ("New name", "اسم", SAT))
        with self.assertRaises(rules.CalendarError):
            rules.rename_holiday(h, " ")
