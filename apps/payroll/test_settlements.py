from datetime import date
from decimal import Decimal

from django.contrib.contenttypes.models import ContentType

from apps.attendance.models import Attendance
from apps.audit.models import AuditEvent
from apps.core import approvals
from apps.core.models import ApprovalFlow, ApprovalRequest
from apps.employees.models import Employee
from apps.leave.models import LeaveLedger, LeaveType
from apps.vehicles.incidents import Fine
from apps.vehicles.models import Vehicle

from . import runs, services, settlements
from . import settlement_rules as rules
from .finalsettlement import FinalSettlement
from .models import AllowanceType, SalaryRecord
from .payrun import PayrollPolicy
from .services import PayrollError
from .test_runs import RunCase
from .tests import VALID_IBAN, make_renewal_payment

D = Decimal
LWD = date(2026, 3, 31)
Status = FinalSettlement.Status


def approval_of(obj):
    return ApprovalRequest.objects.filter(content_type=ContentType.objects.get_for_model(obj), object_id=str(obj.pk)).latest("pk")


class SettleCase(RunCase):
    def setUp(self):
        super().setUp()
        self.annual = LeaveType.objects.create(company=self.co, code="AL", name="Annual leave", annual_entitlement=D("30"),
                                          tracks_balance=True, is_paid=True)
        for emp in (self.ali, self.sara, self.hr_emp):
            LeaveLedger.objects.create(employee=emp, leave_type=self.annual, year=2025, kind="entitlement", days=D("30"))
            LeaveLedger.objects.create(employee=emp, leave_type=self.annual, year=2026, kind="entitlement", days=D("30"))
            self.present(emp)
        LeaveLedger.objects.create(employee=self.ali, leave_type=self.annual, year=2025, kind="taken", days=D("-10"))

    def settle(self, emp=None, **kw):
        args = dict(reason="resignation", last_working_day=LWD, notice_date=date(2026, 3, 1), user=self.hr)
        args.update(kw)
        return settlements.create_settlement(emp or self.ali, **args)

    def amounts(self, case):
        return {i.kind: i.amount for i in case.items.all()}

    def codes(self, case, level=None):
        return sorted(i["code"] for i in case.issues if level in (None, i["level"]))


class CalculationTests(SettleCase):
    def test_a_resignation_with_full_notice_pays_salary_leave_and_the_gratuity_the_employer_owes(self):
        case = self.settle()
        gratuity = rules.gratuity(date(2020, 1, 1), LWD, D("900"))["employer"]
        self.assertEqual(gratuity, D("2400.000"))
        self.assertEqual(self.amounts(case), {"wages": D("900.000"), "leave": D("825.000"), "gratuity": gratuity})
        self.assertEqual((case.total, self.codes(case, "block")), (D("4125.000"), []))
        self.assertEqual(case.details["leave"]["payable"], "27.5")             # 50 balance - 30 granted for 2026 + 7.5 earned
        self.assertEqual(case.details["daily_rate"], "30.000")
        self.assertEqual((case.payment_method, case.iban), ("bank", VALID_IBAN))

    def test_the_sio_part_of_the_gratuity_is_told_not_paid(self):
        case = self.settle()
        info = [i for i in case.issues if i["code"] == "sio_gratuity"][0]
        self.assertEqual(info["level"], "info")
        self.assertIn("The SIO pays the employee about", info["text"])
        self.assertEqual(case.details["gratuity"]["years_before_scheme"], "4.1667")

    def test_a_bahraini_has_no_employer_gratuity_and_pays_sio_on_the_last_month(self):
        case = self.settle(self.sara)
        self.assertEqual(self.amounts(case), {"wages": D("1200.000"), "sio": D("-50.000"), "leave": D("1500.000")})
        self.assertEqual(case.total, D("2650.000"))                              # leave: 37.5 days x 1200 / 30
        self.assertFalse(case.details["gratuity"]["applies"])
        self.assertIn("SIO pension", case.details["gratuity"]["why"])

    def test_a_last_day_inside_the_month_pays_the_days_worked_and_the_leave_earned_by_then(self):
        for d in range(16, 32):
            Attendance.objects.filter(employee=self.ali, date=date(2026, 3, d)).delete()
        case = self.settle(last_working_day=date(2026, 3, 15), notice_date=date(2026, 2, 13))
        self.assertEqual(self.amounts(case)["wages"], D("435.484"))            # 15 days of 900 / 31
        self.assertEqual(self.amounts(case)["leave"], D("780.000"))            # 50 - 30 + 6 earned = 26 days x 30
        self.assertEqual(self.codes(case, "block"), [])

    def test_absence_in_the_last_month_costs_one_thirtieth_a_day(self):
        Attendance.objects.filter(employee=self.ali, date=date(2026, 3, 10)).update(status="absent")
        case = self.settle()
        self.assertEqual(self.amounts(case)["absence"], D("-30.000"))
        self.assertEqual(case.total, D("4095.000"))

    def test_gratuity_is_worked_on_basic_plus_the_social_allowance_only(self):
        social = AllowanceType.objects.create(company=self.co, name="Social allowance", counts_for_gratuity=True)
        omar = self.staff("Omar", "S-7", nationality="Indian", basic="800")
        services.change_salary(omar, effective_from=date(2026, 1, 1), basic=D("800"), reason="increase",
                               allowances=[(social, D("100")), (self.housing, D("300"))])
        LeaveLedger.objects.create(employee=omar, leave_type=self.annual, year=2026, kind="entitlement", days=D("30"))
        self.present(omar)
        case = self.settle(omar)
        self.assertEqual(case.details["gratuity"]["wage"], "900.000")           # 800 basic + 100 social; housing left out
        self.assertEqual(case.items.get(kind="gratuity").amount, D("2400.000"))   # on 900, not on the 1200 total
        self.assertEqual(case.details["basis"]["monthly_total"], "1200.000")

    def test_the_leave_that_counts_is_what_is_earned_not_the_whole_years_grant(self):
        LeaveLedger.objects.filter(employee=self.ali).delete()                  # a person with no leave history at all
        LeaveLedger.objects.create(employee=self.ali, leave_type=self.annual, year=2026, kind="entitlement", days=D("30"))
        self.assertEqual(self.settle().details["leave"]["payable"], "7.5")

    def test_leave_taken_in_advance_is_warned_about_and_never_deducted(self):
        LeaveLedger.objects.create(employee=self.ali, leave_type=self.annual, year=2026, kind="taken", days=D("-60"))
        case = self.settle()
        self.assertIn("leave_advance", self.codes(case, "warn"))
        self.assertNotIn("leave", self.amounts(case))

    def test_leave_booked_after_the_last_day_is_warned_about(self):
        from apps.leave.models import LeaveRequest
        LeaveRequest.objects.create(employee=self.ali, leave_type=self.annual, start_date=date(2026, 4, 10),
                                    end_date=date(2026, 4, 14), days=D("5"), status="approved")
        LeaveLedger.objects.create(employee=self.ali, leave_type=self.annual, year=2026, kind="taken", days=D("-5"))
        case = self.settle()
        self.assertIn("leave_after", self.codes(case, "warn"))
        self.assertEqual(case.details["leave"]["payable"], "27.5")              # the five days are given back


class NoticeTests(SettleCase):
    def test_a_short_notice_by_the_employer_is_paid_in_lieu_automatically(self):
        case = self.settle(reason="termination", notice_date=date(2026, 3, 11))
        self.assertEqual(case.details["notice"], {"required": 30, "served": 20, "short": 10, "treatment": "pay"})
        item = case.items.get(kind="notice")
        self.assertEqual((item.amount, item.label), (D("300.000"), "Pay in lieu of notice (10 days short)"))

    def test_a_short_notice_by_the_employee_costs_nothing_unless_hr_chooses_to_recover_it(self):
        case = self.settle(notice_date=date(2026, 3, 11))
        self.assertNotIn("notice", self.amounts(case))
        case = settlements.update_settlement(case, notice_treatment="recover")
        self.assertEqual(self.amounts(case)["notice"], D("-300.000"))
        case = settlements.update_settlement(case, notice_required_days=60, notice_treatment="recover")
        self.assertEqual(self.amounts(case)["notice"], D("-1200.000"))          # 40 days short

    def test_a_resignation_or_termination_needs_a_notice_date(self):
        for reason in ("resignation", "termination"):
            case = self.settle(self.sara if reason == "resignation" else self.hr_emp, reason=reason, notice_date=None)
            self.assertEqual(self.codes(case, "block"), ["notice_date"], reason)

    def test_other_reasons_have_no_notice_rule(self):
        for emp, reason in ((self.ali, "end_of_contract"), (self.sara, "mutual"), (self.hr_emp, "death")):
            case = self.settle(emp, reason=reason, notice_date=None)
            self.assertNotIn("notice", self.amounts(case))
            self.assertNotIn("notice_date", self.codes(case))


class GratuityDecisionTests(SettleCase):
    def test_a_dismissal_without_notice_must_be_decided(self):
        case = self.settle(reason="summary", notice_date=None)
        self.assertEqual(self.codes(case, "block"), ["gratuity_decision"])
        self.assertNotIn("gratuity", self.amounts(case))
        case = settlements.update_settlement(case, gratuity_decision=True)
        self.assertEqual((self.codes(case, "block"), "gratuity" in self.amounts(case)), ([], True))

    def test_saying_no_gratuity_needs_a_reason_that_is_kept(self):
        case = self.settle(reason="summary", notice_date=None)
        with self.assertRaisesMessage(PayrollError, "Say why no gratuity is payable"):
            settlements.update_settlement(case, gratuity_decision=False)
        case = settlements.update_settlement(case, gratuity_decision=False, gratuity_note="Gross misconduct, Article 107(2)")
        self.assertEqual((self.codes(case, "block"), "gratuity" in self.amounts(case)), ([], False))
        self.assertEqual(case.gratuity_note, "Gross misconduct, Article 107(2)")

    def test_hr_may_override_the_standard_rule_for_other_reasons_too(self):
        case = self.settle(gratuity_decision=False, gratuity_note="Contract waives it")
        self.assertNotIn("gratuity", self.amounts(case))
        self.assertEqual(self.codes(case, "block"), [])

    def test_a_gcc_national_must_be_decided_before_any_gratuity_is_worked(self):
        Employee.objects.filter(pk=self.ali.pk).update(nationality="Omani")
        case = self.settle()
        self.assertEqual(self.codes(case, "block"), ["gratuity_decision"])
        self.assertEqual(settlements.update_settlement(case, gratuity_decision=True).total, D("4125.000"))

    def test_a_nationality_that_cannot_be_read_blocks(self):
        Employee.objects.filter(pk=self.ali.pk).update(nationality="Bahrani")
        case = self.settle()
        self.assertIn("nationality", self.codes(case, "block"))
        self.assertNotIn("gratuity", self.amounts(case))                        # nothing is guessed for an unreadable nationality


class WhatCanStopItTests(SettleCase):
    def test_missing_pay_details_and_records_block_the_case(self):
        newcomer = Employee.objects.create(company=self.co, employee_no="S-9", first_name="New", nationality="Indian",
                                           joining_date=date(2020, 1, 1))
        LeaveLedger.objects.create(employee=newcomer, leave_type=self.annual, year=2026, kind="entitlement", days=D("30"))
        case = self.settle(newcomer)
        self.assertEqual(self.codes(case, "block"), ["no_salary", "payment"])
        self.assertEqual(set(self.amounts(case)), {"wages"})                    # nothing else can be worked out without a wage

    def test_the_company_needs_a_payroll_policy(self):
        PayrollPolicy.objects.all().delete()
        with self.assertRaisesMessage(PayrollError, "has no payroll policy"):
            self.settle()

    def test_the_annual_leave_type_must_be_known_when_it_cannot_be_guessed(self):
        LeaveType.objects.all().update(name="Leave A", code="A")
        LeaveType.objects.create(company=self.co, code="B", name="Leave B", annual_entitlement=D("20"),tracks_balance=True, is_paid=True)
        case = self.settle()
        self.assertIn("leave_type", self.codes(case, "block"))
        case = settlements.update_settlement(case, leave_type=LeaveType.objects.get(code="A"))
        self.assertNotIn("leave_type", self.codes(case))

    def test_no_leave_type_at_all_is_only_a_warning(self):
        LeaveLedger.objects.all().delete()
        LeaveType.objects.all().delete()
        self.assertIn("leave_none", self.codes(self.settle(), "warn"))

    def test_the_person_must_not_owe_more_than_they_are_owed(self):
        case = self.settle()
        case = settlements.add_item(case, kind="deduction", label="Loan balance", amount=D("9000")).settlement
        case.refresh_from_db()
        self.assertEqual(case.total, D("-4875.000"))
        self.assertIn("negative_total", self.codes(case, "block"))
        with self.assertRaisesMessage(PayrollError, "problem to fix first"):
            settlements.submit_settlement(case, self.hr)


class CreationRulesTests(SettleCase):
    def test_the_basic_rules(self):
        with self.assertRaisesMessage(PayrollError, "before the joining date"):
            self.settle(last_working_day=date(2019, 12, 31), notice_date=None)
        with self.assertRaisesMessage(PayrollError, "after the last working day"):
            self.settle(notice_date=date(2026, 4, 5))
        with self.assertRaisesMessage(PayrollError, "more than a year away"):
            self.settle(last_working_day=date(2099, 1, 1), notice_date=None)
        with self.assertRaisesMessage(PayrollError, "Choose why"):
            self.settle(reason="boredom")
        with self.assertRaisesMessage(PayrollError, "between 0 and 365"):
            self.settle(notice_required_days=400)
        self.assertFalse(FinalSettlement.objects.exists())

    def test_one_live_settlement_per_person_but_a_cancelled_one_does_not_count(self):
        case = self.settle()
        with self.assertRaisesMessage(PayrollError, "already has a final settlement"):
            self.settle()
        settlements.cancel_settlement(case)
        self.settle()

    def test_labor_needs_a_profile_and_must_not_be_contractor_supplied(self):
        from .tests import make_profile
        orphan = Employee.objects.create(company=self.co, employee_no="L-1", first_name="Orphan", worker_type="labor",
                                         joining_date=date(2020, 1, 1))
        with self.assertRaisesMessage(PayrollError, "no labor profile"):
            self.settle(orphan)
        contracted = Employee.objects.create(company=self.co, employee_no="L-2", first_name="Contracted",
                                             worker_type="labor", joining_date=date(2020, 1, 1))
        make_profile(contracted, "contracted")
        with self.assertRaisesMessage(PayrollError, "paid through the contractor"):
            self.settle(contracted)

    def test_a_month_that_payroll_has_already_paid_cannot_be_settled_again(self):
        self.approve(self.new_run())
        with self.assertRaisesMessage(PayrollError, "March 2026 payroll has already paid this person"):
            self.settle()
        self.settle(last_working_day=date(2026, 4, 5), notice_date=date(2026, 3, 1))   # April is free

    def test_the_inputs_can_be_changed_only_while_it_is_a_draft(self):
        case = self.settle()
        case = settlements.update_settlement(case, last_working_day=date(2026, 3, 20), notice_date=date(2026, 2, 18))
        self.assertEqual(case.items.get(kind="wages").label, "Salary 01 Mar to 20 Mar 2026")
        settlements.cancel_settlement(case)
        with self.assertRaisesMessage(PayrollError, "cancelled, so it cannot be changed"):
            settlements.update_settlement(case, notice_required_days=45)


class LinesAndChargesTests(SettleCase):
    def test_lines_hr_adds_survive_a_recalculation_and_can_be_removed(self):
        case = self.settle()
        bonus = settlements.add_item(case, kind="addition", label="Agreed bonus", amount=D("100"))
        loan = settlements.add_item(case, kind="deduction", label="Loan balance", amount=D("250.5"))
        case.refresh_from_db()
        self.assertEqual(case.total, D("3974.500"))
        settlements.calculate_settlement(case)
        self.assertEqual(case.items.filter(is_system=False).count(), 2)
        settlements.remove_item(loan)
        case.refresh_from_db()
        self.assertEqual(case.total, D("4225.000"))
        with self.assertRaisesMessage(PayrollError, "worked out by the system"):
            settlements.remove_item(case.items.get(kind="wages"))
        self.assertEqual(bonus.amount, D("100"))

    def test_the_rules_for_a_line(self):
        case = self.settle()
        for bad in (D("0"), D("-5"), D("1.2345")):
            with self.assertRaises(PayrollError):
                settlements.add_item(case, kind="addition", label="x", amount=bad)
        with self.assertRaisesMessage(PayrollError, "Say what it is for"):
            settlements.add_item(case, kind="addition", label=" ", amount=D("5"))
        with self.assertRaisesMessage(PayrollError, "addition or a deduction"):
            settlements.add_item(case, kind="wages", label="x", amount=D("5"))

    def test_pending_charges_are_picked_up_once_and_shared_with_payroll_runs(self):
        vehicle = Vehicle.objects.create(company=self.co, plate_number="123456")
        fine = Fine.objects.create(vehicle=vehicle, driver=self.ali, fined_on=date(2026, 3, 8), reference="F1",
                                   offence="Speeding", amount=D("20"), charged_to_employee=True)
        make_renewal_payment(self.ali, description="Residence renewal", government_fee=D("40"), service_fee=D("10.5"),
                             charged_to_employee=True)
        case = self.settle()
        self.assertEqual(len(settlements.charge_candidates(case, self.hr)), 2)
        self.assertEqual(settlements.import_charges(case, self.hr), 2)
        self.assertEqual(settlements.import_charges(case, self.hr), 0)
        case.refresh_from_db()
        self.assertEqual(case.total, D("4125.000") - D("70.500"))
        self.assertEqual(fine.recovered_on, None)                                 # recovered only once it is approved

    def test_cancelling_the_settlement_frees_the_charges(self):
        make_renewal_payment(self.ali, government_fee=D("40"), charged_to_employee=True)
        case = self.settle()
        settlements.import_charges(case, self.hr)
        settlements.cancel_settlement(case)
        later = self.settle()
        self.assertEqual(len(settlements.charge_candidates(later, self.hr)), 1)


class ApprovalTests(SettleCase):
    def test_the_second_approval_flow_exists(self):
        flow = ApprovalFlow.objects.get(code="payroll.settlement")
        self.assertEqual((flow.steps.get().group, flow.name), (self.management, "Final settlement approval"))

    def test_approval_marks_the_person_separated_and_recovers_the_fines(self):
        vehicle = Vehicle.objects.create(company=self.co, plate_number="123456")
        fine = Fine.objects.create(vehicle=vehicle, driver=self.ali, fined_on=date(2026, 3, 8), reference="F1",
                                   offence="Speeding", amount=D("20"), charged_to_employee=True)
        case = self.settle()
        settlements.import_charges(case, self.hr)
        settlements.submit_settlement(case, self.hr)
        case.refresh_from_db()
        self.assertEqual(case.status, Status.SUBMITTED)
        with self.assertRaisesMessage(PayrollError, "waiting for approval"):
            settlements.calculate_settlement(case)
        with self.assertRaises(approvals.ApprovalError):
            approvals.decide(approval_of(case).pk, self.hr, "approve")           # the sender cannot approve it
        approvals.decide(approval_of(case).pk, self.boss, "approve")
        case.refresh_from_db()
        self.ali.refresh_from_db()
        fine.refresh_from_db()
        self.assertEqual((case.status, self.ali.status, case.note), (Status.APPROVED, "separated", ""))
        self.assertIsNotNone(case.approved_at)
        self.assertEqual(fine.recovered_on, LWD)
        with self.assertRaisesMessage(PayrollError, "approved, so it cannot be recalculated"):
            settlements.calculate_settlement(case)
        with self.assertRaisesMessage(PayrollError, "cannot be cancelled"):
            settlements.cancel_settlement(case)

    def test_a_rejected_case_returns_to_draft_with_the_reason(self):
        case = self.settle()
        settlements.submit_settlement(case, self.hr)
        approvals.decide(approval_of(case).pk, self.boss, "reject", comment="Check the leave days")
        case.refresh_from_db()
        self.ali.refresh_from_db()
        self.assertEqual((case.status, case.note, self.ali.status), (Status.DRAFT, "Rejected: Check the leave days", "active"))
        settlements.calculate_settlement(case)
        case.refresh_from_db()
        self.assertEqual(case.note, "")

    def test_taking_it_back(self):
        case = self.settle()
        settlements.submit_settlement(case, self.hr)
        case = settlements.withdraw_settlement(case, self.hr)
        self.assertEqual((case.status, case.note), (Status.DRAFT, "Taken back for changes."))
        with self.assertRaisesMessage(PayrollError, "not waiting for approval"):
            settlements.withdraw_settlement(case, self.hr)

    def test_the_sender_needs_an_employee_record(self):
        case = self.settle()
        with self.assertRaisesMessage(PayrollError, "not linked to an employee record"):
            settlements.submit_settlement(case, self.hr_no_emp)

    def test_payment_is_recorded_once_approved(self):
        case = self.settle()
        with self.assertRaisesMessage(PayrollError, "only for an approved settlement"):
            settlements.mark_paid(case, date(2026, 4, 2))
        settlements.submit_settlement(case, self.hr)
        approvals.decide(approval_of(case).pk, self.boss, "approve")
        case.refresh_from_db()
        with self.assertRaisesMessage(PayrollError, "cannot be in the future"):
            settlements.mark_paid(case, date(2099, 1, 1))
        settlements.mark_paid(case, date(2026, 4, 2))
        case.refresh_from_db()
        self.assertEqual((case.status, case.paid_on), (Status.PAID, date(2026, 4, 2)))

    def test_the_figures_and_the_iban_are_hidden_in_the_audit_log(self):
        case = self.settle()
        settlements.add_item(case, kind="addition", label="Agreed bonus", amount=D("123.456"))
        text = str([e.changes for e in AuditEvent.objects.filter(
            module="payroll", content_type__model__in=["finalsettlement", "settlementitem"])])
        for secret in ("123.456", "4125", "825.000", "2400.000", VALID_IBAN):
            self.assertNotIn(secret, text)


class PayrollRunIntegrationTests(SettleCase):
    def test_a_person_with_a_settlement_is_left_out_of_the_runs_that_it_pays(self):
        self.settle(last_working_day=date(2026, 3, 15), notice_date=date(2026, 2, 13))
        self.assertEqual(settlements.settled_employee_ids(self.co.pk, date(2026, 3, 31)), {self.ali.pk})
        self.assertEqual(settlements.settled_employee_ids(self.co.pk, date(2026, 2, 28)), set())
        names = set(self.new_run().lines.values_list("employee_name", flat=True))
        self.assertEqual(names, {"Sara", "Hana"})                                # March: Ali's settlement pays him
        self.present(self.ali, 2026, 2)
        for e in (self.sara, self.hr_emp):
            self.present(e, 2026, 2)
        february = set(runs.create_run(self.co, 2026, 2, user=self.hr).lines.values_list("employee_name", flat=True))
        self.assertEqual(february, {"Ali", "Sara", "Hana"})                      # February is still paid in the normal run

    def test_a_run_made_before_the_settlement_cannot_be_sent_with_that_person_in_it(self):
        run = self.new_run()
        self.assertEqual(run.lines.count(), 3)
        self.settle()
        with self.assertRaisesMessage(PayrollError, "final settlement that pays this month"):
            runs.submit_run(run, self.hr)
        runs.calculate_run(run)
        self.assertEqual(run.lines.count(), 2)
        runs.submit_run(run, self.hr)

    def test_a_cancelled_settlement_puts_the_person_back_in_the_run(self):
        case = self.settle()
        settlements.cancel_settlement(case)
        self.assertIn("Ali", set(self.new_run().lines.values_list("employee_name", flat=True)))


class GratuityContributionTests(SettleCase):
    def newbie(self):
        social = AllowanceType.objects.create(company=self.co, name="Social allowance", counts_for_gratuity=True)
        emp = self.staff("Newbie", "S-8", nationality="Indian", basic="500")
        Employee.objects.filter(pk=emp.pk).update(joining_date=date(2025, 6, 1))
        SalaryRecord.objects.filter(employee=emp).delete()
        services.change_salary(emp, effective_from=date(2025, 6, 1), basic=D("500"), reason="joining",
                               allowances=[(social, D("100")), (self.housing, D("200"))])
        self.present(emp)
        return emp

    def test_each_non_bahraini_line_shows_the_contribution_the_company_pays_the_sio(self):
        newbie = self.newbie()
        run = self.new_run()
        ali, new, sara = (run.lines.get(employee=e) for e in (self.ali, newbie, self.sara))
        self.assertEqual(ali.breakdown["eosg"], {"rate": "8.4", "wage": "900.000", "amount": "75.600"})   # over three years
        self.assertEqual(new.breakdown["eosg"], {"rate": "4.2", "wage": "600.000", "amount": "25.200"})   # basic + social only
        self.assertIsNone(sara.breakdown["eosg"])                                                       # Bahraini: SIO pension
        self.assertEqual(runs.totals(run)["eosg"], D("100.800"))

    def test_a_gcc_national_has_no_sio_gratuity_contribution(self):
        Employee.objects.filter(pk=self.ali.pk).update(nationality="Omani")
        self.assertIsNone(self.new_run().lines.get(employee=self.ali).breakdown["eosg"])

    def test_the_register_carries_the_contribution_for_finance(self):
        import io, zipfile
        run = self.new_run()
        from . import outputs
        with zipfile.ZipFile(io.BytesIO(outputs.register(run))) as z:
            sheet = z.read("xl/worksheets/sheet1.xml").decode()
        self.assertIn("SIO gratuity (company)", sheet)
        self.assertIn("75.6", sheet)
