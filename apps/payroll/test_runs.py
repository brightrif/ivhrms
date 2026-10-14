from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase

from apps.attendance.models import Attendance
from apps.audit.models import AuditEvent
from apps.core import approvals
from apps.core.models import ApprovalFlow
from apps.employees.models import Employee
from apps.organization.models import Company, CompanyAccess
from apps.vehicles.incidents import Fine
from apps.vehicles.models import Vehicle

from . import policy as policies
from . import runs, services
from .models import AllowanceType
from .payrun import PayrollAdjustment, PayrollLine, PayrollPolicy, PayrollRun
from .services import PayrollError
from .tests import VALID_IBAN, make_profile, make_renewal_payment

D = Decimal
User = get_user_model()
MARCH = (2026, 3)
Status = PayrollRun.Status


class RunCase(TestCase):
    def setUp(self):
        self.co = Company.objects.create(code="IV1", name="IV One")
        self.other = Company.objects.create(code="IV2", name="IV Two")
        self.management = Group.objects.get(name="Management")
        self.boss = self.user("boss", "Management")
        self.hr = self.user("hr")
        self.hr_emp = Employee.objects.create(company=self.co, employee_no="H-1", first_name="Hana", user=self.hr,
                                              joining_date=date(2019, 1, 1), nationality="Bahraini")
        self.hr_no_emp = self.user("hr_no_employee")
        self.policy = policies.set_policy(self.co, effective_from=date(2020, 1, 1), sio_employee_rate=D("5"),
                                          sio_employer_rate=D("10"))
        self.housing = AllowanceType.objects.create(company=self.co, name="Housing")
        services.change_salary(self.hr_emp, effective_from=date(2020, 1, 1), basic=D("700"), reason="joining", allowances=[])
        services.save_payment_details(self.hr_emp, method="cash")
        self.ali = self.staff("Ali", "S-1", nationality="Indian", basic="900")
        self.sara = self.staff("Sara", "S-2", nationality="Bahraini", basic="1000", housing="200")

    def user(self, name, group=None):
        u = User.objects.create_user(name)
        if group:
            u.groups.add(Group.objects.get(name=group))
        CompanyAccess.objects.create(user=u, company=self.co)
        return u

    def staff(self, first, no, *, nationality, basic, housing=None, method="bank", company=None):
        emp = Employee.objects.create(company=company or self.co, employee_no=no, first_name=first,
                                      nationality=nationality, joining_date=date(2020, 1, 1))
        services.change_salary(emp, effective_from=date(2020, 1, 1), basic=D(basic), reason="joining",
                               allowances=[(self.housing, D(housing))] if housing else [])
        if method == "bank":
            services.save_payment_details(emp, method="bank", bank_name="NBB", iban=VALID_IBAN)
        else:
            services.save_payment_details(emp, method="cash")
        return emp

    def present(self, emp, year=2026, month=3, absent=()):
        import calendar
        for day in range(1, calendar.monthrange(year, month)[1] + 1):
            d = date(year, month, day)
            status = "weekly_off" if d.weekday() == 4 else "absent" if day in absent else "present"
            Attendance.objects.update_or_create(employee=emp, date=d, defaults={"status": status})

    def new_run(self, *, user=None):
        for e in (self.ali, self.sara, self.hr_emp):
            self.present(e)
        return runs.create_run(self.co, *MARCH, user=user or self.hr)

    def line(self, run, emp):
        return PayrollLine.objects.get(run=run, employee=emp)

    def approve(self, run):
        runs.submit_run(run, self.hr)
        req = approvals_of(run)
        approvals.decide(req.pk, self.boss, "approve")
        run.refresh_from_db()
        return run


def approvals_of(run):
    from django.contrib.contenttypes.models import ContentType
    from apps.core.models import ApprovalRequest
    return ApprovalRequest.objects.filter(content_type=ContentType.objects.get_for_model(PayrollRun),
                                          object_id=str(run.pk)).latest("pk")


class PolicyTests(RunCase):
    def test_a_new_policy_ends_the_old_one_the_day_before(self):
        new = policies.set_policy(self.co, effective_from=date(2026, 1, 1), days_in_month=30, sio_employee_rate=D("6"),
                                  sio_employer_rate=D("11"))
        self.policy.refresh_from_db()
        self.assertEqual(self.policy.effective_to, date(2025, 12, 31))
        self.assertEqual(policies.policy_on(self.co, date(2025, 12, 31)), self.policy)
        self.assertEqual(policies.policy_on(self.co, date(2026, 1, 1)), new)
        self.assertIsNone(policies.policy_on(self.co, date(2019, 12, 31)))

    def test_the_rules(self):
        with self.assertRaisesMessage(PayrollError, "must start after the current one"):
            policies.set_policy(self.co, effective_from=date(2020, 1, 1))
        with self.assertRaisesMessage(PayrollError, "between 20 and 31"):
            policies.set_policy(self.co, effective_from=date(2026, 1, 1), days_in_month=40)
        with self.assertRaisesMessage(PayrollError, "between 0 and 100"):
            policies.set_policy(self.co, effective_from=date(2026, 1, 1), sio_employee_rate=D("150"))
        with self.assertRaisesMessage(PayrollError, "cannot be below the lowest"):
            policies.set_policy(self.co, effective_from=date(2026, 1, 1), sio_wage_floor=D("500"), sio_wage_cap=D("100"))
        self.assertEqual(PayrollPolicy.objects.filter(company=self.co).count(), 1)

    def test_the_sio_rates_can_be_left_empty_for_now(self):
        empty = policies.set_policy(self.other, effective_from=date(2026, 1, 1))
        self.assertFalse(empty.sio_rates_set)
        self.assertTrue(self.policy.sio_rates_set)

    def test_a_policy_cannot_start_inside_an_approved_month(self):
        run = self.approve(self.new_run())
        with self.assertRaisesMessage(PayrollError, "already approved"):
            policies.set_policy(self.co, effective_from=date(2026, 3, 15))
        policies.set_policy(self.co, effective_from=date(2026, 4, 1), sio_employee_rate=D("5"), sio_employer_rate=D("10"))
        self.assertEqual(run.policy_values["days_in_month"], 30)


class CreateAndCalculateTests(RunCase):
    def test_a_run_has_a_line_for_each_staff_member_with_the_workings(self):
        run = self.new_run()
        self.assertEqual((run.status, run.label, run.period_end), (Status.DRAFT, "March 2026", date(2026, 3, 31)))
        self.assertEqual(run.lines.count(), 3)                                      # Ali, Sara and the HR officer
        sara = self.line(run, self.sara)
        self.assertEqual((sara.gross, sara.sio_wage, sara.sio_employee, sara.sio_employer, sara.net),
                         (D("1200.000"), D("1000.000"), D("50.000"), D("100.000"), D("1150.000")))   # housing does not count
        self.assertEqual(self.line(run, self.ali).sio_employee, D("0.000"))                     # Ali is not Bahraini
        self.assertEqual((sara.nationality_kind, sara.payment_method, sara.bank_name, sara.iban),
                         ("bahraini", "bank", "NBB", VALID_IBAN))
        self.assertEqual(self.line(run, self.ali).nationality_kind, "other")

    def test_sio_uses_the_flagged_allowances(self):
        AllowanceType.objects.filter(pk=self.housing.pk).update(counts_for_sio=True)
        sara = self.line(self.new_run(), self.sara)
        self.assertEqual((sara.sio_wage, sara.sio_employee, sara.sio_employer, sara.net),
                         (D("1200.000"), D("60.000"), D("120.000"), D("1140.000")))

    def test_absence_comes_from_attendance_at_one_thirtieth(self):
        self.present(self.ali, absent=(10,))
        self.present(self.sara)
        self.present(self.hr_emp)
        run = runs.create_run(self.co, *MARCH, user=self.hr)
        ali = self.line(run, self.ali)
        self.assertEqual((ali.unpaid_days, ali.absence_deduction, ali.net), (D("1.00"), D("30.000"), D("870.000")))

    def test_who_is_in_the_run(self):
        Employee.objects.create(company=self.co, employee_no="S-3", first_name="Notice", joining_date=date(2020, 1, 1),
                                status="on_notice")
        Employee.objects.create(company=self.co, employee_no="S-4", first_name="Gone", joining_date=date(2020, 1, 1),
                                status="separated")
        Employee.objects.create(company=self.co, employee_no="S-5", first_name="Later", joining_date=date(2026, 4, 5))
        Employee.objects.create(company=self.other, employee_no="X-1", first_name="Foreign", joining_date=date(2020, 1, 1))
        labor = Employee.objects.create(company=self.co, employee_no="L-1", first_name="Ravi", worker_type="labor",
                                        joining_date=date(2020, 1, 1))
        make_profile(labor, "direct")
        names = set(self.new_run().lines.values_list("employee_name", flat=True))
        self.assertEqual(names, {"Ali", "Sara", "Hana", "Notice"})

    def test_creating_rules(self):
        with self.assertRaisesMessage(PayrollError, "has not started yet"):
            runs.create_run(self.co, 2099, 1, user=self.hr)
        with self.assertRaisesMessage(PayrollError, "no payroll policy"):
            runs.create_run(self.other, *MARCH, user=self.hr)
        with self.assertRaisesMessage(PayrollError, "Choose a month"):
            runs.create_run(self.co, 2026, 13, user=self.hr)
        first = self.new_run()
        with self.assertRaisesMessage(PayrollError, "already has a staff payroll run for March 2026"):
            runs.create_run(self.co, *MARCH, user=self.hr)
        runs.cancel_run(first)
        again = runs.create_run(self.co, *MARCH, user=self.hr)                      # a cancelled run frees the month
        self.assertNotEqual(again.pk, first.pk)

    def test_recalculating_picks_up_changes_and_keeps_adjustments(self):
        run = self.new_run()
        runs.add_adjustment(run, self.ali, kind="addition", category="bonus", label="Eid bonus", amount=D("50"))
        Attendance.objects.filter(employee=self.ali, date=date(2026, 3, 10)).update(status="absent")
        services.save_payment_details(self.ali, method="cash")
        runs.calculate_run(run)
        ali = self.line(run, self.ali)
        self.assertEqual((ali.absence_deduction, ali.additions, ali.net, ali.payment_method),
                         (D("30.000"), D("50.000"), D("920.000"), "cash"))
        self.assertEqual(run.lines.count(), 3)                                      # recalculating twice adds no one
        runs.calculate_run(run)
        self.assertEqual(run.lines.count(), 3)

    def test_a_person_who_left_since_drops_out_on_recalculation(self):
        run = self.new_run()
        Employee.objects.filter(pk=self.ali.pk).update(status="separated")
        runs.calculate_run(run)
        self.assertFalse(run.lines.filter(employee=self.ali).exists())

    def test_blocking_problems_are_listed_on_the_line(self):
        Employee.objects.filter(pk=self.sara.pk).update(nationality="Bahrani")
        PayrollPolicy.objects.filter(pk=self.policy.pk).update(sio_employee_rate=None)
        newbie = Employee.objects.create(company=self.co, employee_no="S-9", first_name="New", nationality="Indian",
                                         joining_date=date(2020, 1, 1))
        run = self.new_run()
        self.assertEqual([i["code"] for i in self.line(run, self.sara).blocking], ["nationality"])
        self.assertEqual(sorted(i["code"] for i in self.line(run, newbie).blocking), ["no_salary", "payment"])
        self.assertEqual(runs.blocking_count(run), 3)             # Sara (nationality), the newcomer, the HR officer (no rates)


class AdjustmentTests(RunCase):
    def test_adding_and_removing_changes_the_net(self):
        run = self.new_run()
        bonus = runs.add_adjustment(run, self.ali, kind="addition", category="bonus", label="Eid bonus", amount=D("50"))
        advance = runs.add_adjustment(run, self.ali, kind="deduction", category="advance", label="Advance", amount=D("100"))
        self.assertEqual(self.line(run, self.ali).net, D("850.000"))
        runs.remove_adjustment(advance)
        self.assertEqual(self.line(run, self.ali).net, D("950.000"))
        runs.remove_adjustment(bonus)
        self.assertEqual(self.line(run, self.ali).net, D("900.000"))

    def test_the_rules(self):
        run = self.new_run()
        outsider = Employee.objects.create(company=self.co, employee_no="S-8", first_name="Out", joining_date=date(2027, 1, 1))
        with self.assertRaisesMessage(PayrollError, "is not in this run"):
            runs.add_adjustment(run, outsider, kind="addition", category="bonus", label="x", amount=D("5"))
        for bad in (D("0"), D("-5"), D("1.2345")):
            with self.assertRaises(PayrollError):
                runs.add_adjustment(run, self.ali, kind="addition", category="bonus", label="x", amount=bad)
        with self.assertRaisesMessage(PayrollError, "Say what it is for"):
            runs.add_adjustment(run, self.ali, kind="addition", category="bonus", label="  ", amount=D("5"))
        with self.assertRaisesMessage(PayrollError, "Choose whether"):
            runs.add_adjustment(run, self.ali, kind="gift", category="bonus", label="x", amount=D("5"))
        self.assertFalse(PayrollAdjustment.objects.exists())

    def test_a_deduction_bigger_than_the_pay_blocks_the_run(self):
        run = self.new_run()
        big = runs.add_adjustment(run, self.ali, kind="deduction", category="penalty", label="Big", amount=D("2000"))
        self.assertIn("negative_net", [i["code"] for i in self.line(run, self.ali).blocking])
        runs.remove_adjustment(big)
        self.assertNotIn("negative_net", [i["code"] for i in self.line(run, self.ali).issues])


class ChargeTests(RunCase):
    def setUp(self):
        super().setUp()
        self.vehicle = Vehicle.objects.create(company=self.co, plate_number="123456")
        self.payment = make_renewal_payment(self.ali, description="Residence renewal", paid_on=date(2026, 3, 5),
                                            government_fee=D("40"), service_fee=D("10.500"), charged_to_employee=True)
        self.fine = Fine.objects.create(vehicle=self.vehicle, driver=self.sara, fined_on=date(2026, 3, 8), reference="F1",
                                        offence="Speeding", amount=D("20"), charged_to_employee=True)
        Fine.objects.create(vehicle=self.vehicle, driver=self.sara, fined_on=date(2026, 3, 9), reference="F2",
                            offence="Parking", amount=D("5"))                           # nobody asked the driver to pay
        make_renewal_payment(self.ali, task=self.payment.task, paid_on=date(2026, 3, 6),
                             government_fee=D("9"))                                     # not charged to the employee

    def test_pending_charges_become_deductions_once(self):
        run = self.new_run()
        self.assertEqual(len(runs.charge_candidates(run, self.hr)), 2)
        self.assertEqual(runs.import_charges(run, self.hr), 2)
        self.assertEqual(runs.import_charges(run, self.hr), 0)                          # nothing is picked up twice
        ali, sara = self.line(run, self.ali), self.line(run, self.sara)
        self.assertEqual((ali.deductions, ali.net), (D("50.500"), D("849.500")))
        self.assertEqual((sara.deductions, sara.net), (D("20.000"), D("1130.000")))
        self.assertEqual({a.category for a in run.adjustments.all()}, {"renewal", "fine"})

    def test_a_charge_in_one_live_run_is_not_offered_to_another_until_that_run_is_cancelled(self):
        first = self.new_run()
        runs.import_charges(first, self.hr)
        for e in (self.ali, self.sara, self.hr_emp):
            self.present(e, 2026, 4)
        second = runs.create_run(self.co, 2026, 4, user=self.hr)
        self.assertEqual(runs.charge_candidates(second, self.hr), [])
        runs.cancel_run(first)
        self.assertEqual(len(runs.charge_candidates(second, self.hr)), 2)

    def test_removing_a_charge_makes_it_available_again(self):
        run = self.new_run()
        runs.import_charges(run, self.hr)
        runs.remove_adjustment(run.adjustments.get(source="fine"))
        self.assertEqual([c[0] for c in runs.charge_candidates(run, self.hr)], ["fine"])

    def test_charges_after_the_month_or_for_people_not_in_the_run_wait(self):
        Fine.objects.create(vehicle=self.vehicle, driver=self.sara, fined_on=date(2026, 4, 2), reference="F3",
                            offence="Late", amount=D("7"), charged_to_employee=True)
        stranger = Employee.objects.create(company=self.co, employee_no="S-7", first_name="Stranger",
                                           joining_date=date(2027, 1, 1))
        Fine.objects.create(vehicle=self.vehicle, driver=stranger, fined_on=date(2026, 3, 2), reference="F4",
                            offence="Late", amount=D("7"), charged_to_employee=True)
        self.assertEqual(len(runs.charge_candidates(self.new_run(), self.hr)), 2)

    def test_approving_the_run_marks_the_fines_as_recovered(self):
        run = self.new_run()
        runs.import_charges(run, self.hr)
        self.approve(run)
        self.fine.refresh_from_db()
        self.assertEqual(self.fine.recovered_on, date(2026, 3, 31))
        self.assertIn("March 2026", self.fine.recovered_note)
        self.assertEqual(run.note, "")


class ApprovalTests(RunCase):
    def test_the_approval_flow_exists_after_migrate_and_management_is_the_approver(self):
        flow = ApprovalFlow.objects.get(code="payroll.run")
        step = flow.steps.get()
        self.assertEqual((step.group, step.approver_type), (self.management, "group"))

    def test_sending_it_off_and_approving_it(self):
        run = self.new_run()
        runs.submit_run(run, self.hr)
        run.refresh_from_db()
        self.assertEqual(run.status, Status.SUBMITTED)
        with self.assertRaisesMessage(PayrollError, "waiting for approval"):
            runs.calculate_run(run)                                                   # frozen while it waits
        with self.assertRaisesMessage(PayrollError, "waiting for approval"):
            runs.add_adjustment(run, self.ali, kind="addition", category="bonus", label="x", amount=D("5"))
        with self.assertRaises(approvals.ApprovalError):
            approvals.decide(approvals_of(run).pk, self.hr, "approve")                # HR cannot approve its own run
        approvals.decide(approvals_of(run).pk, self.boss, "approve")
        run.refresh_from_db()
        self.assertEqual(run.status, Status.APPROVED)
        self.assertIsNotNone(run.approved_at)
        with self.assertRaisesMessage(PayrollError, "approved"):
            runs.calculate_run(run)
        with self.assertRaisesMessage(PayrollError, "approved"):
            runs.cancel_run(run)

    def test_a_run_with_problems_cannot_be_sent(self):
        run = self.new_run()
        PayrollPolicy.objects.filter(pk=self.policy.pk).update(sio_employee_rate=None)
        runs.calculate_run(run)
        with self.assertRaisesMessage(PayrollError, "a problem to fix first"):
            runs.submit_run(run, self.hr)
        run.refresh_from_db()
        self.assertEqual(run.status, Status.DRAFT)

    def test_the_sender_needs_an_employee_record(self):
        run = self.new_run()
        with self.assertRaisesMessage(PayrollError, "not linked to an employee record"):
            runs.submit_run(run, self.hr_no_emp)

    def test_a_rejected_run_goes_back_to_draft_with_the_reason(self):
        run = self.new_run()
        runs.submit_run(run, self.hr)
        approvals.decide(approvals_of(run).pk, self.boss, "reject", comment="Check Ali's absence")
        run.refresh_from_db()
        self.assertEqual((run.status, run.note), (Status.DRAFT, "Rejected: Check Ali's absence"))
        runs.calculate_run(run)                                                       # and it can be worked on again
        run.refresh_from_db()
        self.assertEqual(run.note, "")
        runs.submit_run(run, self.hr)

    def test_taking_it_back(self):
        run = self.new_run()
        runs.submit_run(run, self.hr)
        back = runs.withdraw_run(run, self.hr)
        self.assertEqual((back.status, back.note), (Status.DRAFT, "Taken back for changes."))
        with self.assertRaisesMessage(PayrollError, "not waiting for approval"):
            runs.withdraw_run(back, self.hr)

    def test_only_a_draft_can_be_sent_or_cancelled(self):
        run = self.new_run()
        runs.submit_run(run, self.hr)
        with self.assertRaisesMessage(PayrollError, "cannot be sent for approval"):
            runs.submit_run(run, self.hr)
        with self.assertRaisesMessage(PayrollError, "cannot be cancelled"):
            runs.cancel_run(run)


class FreezeTests(RunCase):
    def approved(self):
        return self.approve(self.new_run())

    def test_pay_that_was_approved_cannot_be_rewritten_by_salary_changes(self):
        run = self.approved()
        self.assertEqual(services.salary_locked_through(self.ali), date(2026, 3, 31))
        with self.assertRaisesMessage(PayrollError, "already approved"):
            services.change_salary(self.ali, effective_from=date(2026, 3, 20), basic=D("950"), reason="increase", allowances=[])
        with self.assertRaisesMessage(PayrollError, "already approved"):
            services.change_salary(self.ali, effective_from=date(2026, 3, 31), basic=D("950"), reason="increase", allowances=[])
        record = services.salary_on(self.ali, date(2026, 3, 1))
        with self.assertRaisesMessage(PayrollError, "cannot be changed"):
            services.correct_salary(record, basic=D("950"), allowances=[], remarks="typo")
        services.change_salary(self.ali, effective_from=date(2026, 4, 1), basic=D("950"), reason="increase", allowances=[])
        self.assertEqual(self.line(run, self.ali).gross, D("900.000"))                # March did not move

    def test_a_draft_run_does_not_lock_anything(self):
        self.new_run()
        self.assertIsNone(services.salary_locked_through(self.ali))
        record = services.salary_on(self.ali, date(2026, 3, 1))
        services.correct_salary(record, basic=D("910"), allowances=[], remarks="typo")

    def test_a_cancelled_run_unlocks_again(self):
        run = self.new_run()
        runs.cancel_run(run)
        self.assertIsNone(services.salary_locked_through(self.ali))


class PaymentTests(RunCase):
    def setUp(self):
        super().setUp()
        self.run = self.approve(self.new_run())

    def test_paying_the_bank_and_cash_lines_in_turn_finishes_the_run(self):
        bank = list(runs.unpaid_lines(self.run, "bank"))
        cash = list(runs.unpaid_lines(self.run, "cash"))
        self.assertEqual((len(bank), len(cash)), (2, 1))
        self.assertEqual(runs.mark_paid(self.run, [l.pk for l in bank], date(2026, 4, 2)), 2)
        self.run.refresh_from_db()
        self.assertEqual(self.run.status, Status.APPROVED)                           # cash still to pay
        self.assertEqual(runs.mark_paid(self.run, [cash[0].pk], date(2026, 4, 3)), 1)
        self.run.refresh_from_db()
        self.assertEqual(self.run.status, Status.PAID)
        self.assertEqual(runs.totals(self.run)["unpaid"], 0)

    def test_the_rules(self):
        line = self.run.lines.first()
        with self.assertRaisesMessage(PayrollError, "cannot be in the future"):
            runs.mark_paid(self.run, [line.pk], date(2099, 1, 1))
        with self.assertRaisesMessage(PayrollError, "before the month starts"):
            runs.mark_paid(self.run, [line.pk], date(2026, 2, 28))
        with self.assertRaisesMessage(PayrollError, "at least one person"):
            runs.mark_paid(self.run, [], date(2026, 4, 2))
        runs.mark_paid(self.run, [line.pk], date(2026, 4, 2))
        with self.assertRaisesMessage(PayrollError, "at least one person"):
            runs.mark_paid(self.run, [line.pk], date(2026, 4, 3))                    # already paid
        draft = self.new_draft_elsewhere()
        with self.assertRaisesMessage(PayrollError, "approved run"):
            runs.mark_paid(draft, [draft.lines.first().pk], date(2026, 4, 2))

    def new_draft_elsewhere(self):
        for e in (self.ali, self.sara, self.hr_emp):
            self.present(e, 2026, 4)
        return runs.create_run(self.co, 2026, 4, user=self.hr)


class AuditAndTotalsTests(RunCase):
    def test_the_figures_and_the_iban_are_hidden_in_the_audit_log(self):
        run = self.new_run()
        runs.add_adjustment(run, self.ali, kind="addition", category="bonus", label="Eid", amount=D("123.456"))
        events = AuditEvent.objects.filter(module="payroll", content_type__model__in=["payrollline", "payrolladjustment"])
        self.assertTrue(events.exists())
        text = str([e.changes for e in events])
        for secret in ("123.456", "1200", VALID_IBAN, "900.000"):
            self.assertNotIn(secret, text)
        line_event = events.filter(content_type__model="payrollline", action="create").first()
        self.assertEqual(line_event.changes["net"], {"masked": True})
        self.assertEqual(line_event.company_id, self.co.pk)
        self.assertIsNotNone(line_event.subject_employee_id)

    def test_the_run_totals(self):
        run = self.new_run()
        runs.add_adjustment(run, self.ali, kind="addition", category="bonus", label="Eid", amount=D("100"))
        t = runs.totals(run)
        self.assertEqual((t["people"], t["gross"], t["additions"]), (3, D("2800.000"), D("100.000")))
        self.assertEqual((t["sio_employee"], t["sio_employer"]), (D("85.000"), D("170.000")))    # Sara 50/100, the HR officer 35/70
        self.assertEqual((t["net"], t["bank"], t["cash"]), (D("2815.000"), D("2150.000"), D("665.000")))
        self.assertEqual((t["blocking"], t["unpaid"]), (0, 3))
