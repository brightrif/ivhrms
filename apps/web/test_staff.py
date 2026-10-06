from datetime import date, time
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.attendance.tests import BaseCase
from apps.audit.models import AuditEvent
from apps.employees.models import Employee, EmployeeNumberSequence
from apps.leave.models import LeaveLedger, LeaveRequest, LeaveType
from apps.organization.models import Company, Department, Designation
from apps.scheduling.models import Shift, ShiftAssignment


class StaffTests(BaseCase):
    def setUp(self):
        super().setUp()
        self.dept = Department.objects.create(company=self.co, code="OPS", name="Operations")
        self.desig = Designation.objects.create(name="Officer")
        self.shift = Shift.objects.create(company=self.co, code="GEN", name="General",
                                          start_time=time(8, 0), end_time=time(17, 0))
        self.create_url = reverse("web:employee_create")
        self.client.force_login(self.hr_user)

    def payload(self, **over):
        data = {"company": self.co.pk, "worker_type": "staff",
                "employment_type": "permanent", "joining_date": timezone.localdate().isoformat(),
                "first_name": "Sara", "last_name": "Ali", "department": self.dept.pk,
                "designation": self.desig.pk}
        data.update(over)
        return data

    def make(self, **over):
        """Create through the form and return the new employee (fails loudly if nothing was created)."""
        before = set(Employee.objects.values_list("pk", flat=True))
        self.client.post(self.create_url, self.payload(**over))
        return Employee.objects.exclude(pk__in=before).get()

    def test_non_hr_user_is_forbidden(self):
        self.client.force_login(self.emp_user)
        self.assertEqual(self.client.get(reverse("web:employee_list")).status_code, 403)
        self.assertEqual(self.client.get(self.create_url).status_code, 403)

    def test_create_staff_member_end_to_end(self):
        r = self.client.post(self.create_url, self.payload())
        emp = Employee.objects.get(employee_no="C1-0001")
        self.assertRedirects(r, reverse("web:employee_detail", args=[emp.pk]))
        self.assertEqual(emp.history.get().reason, "joining")
        self.assertFalse(ShiftAssignment.objects.filter(employee=emp).exists())   # shifts are assigned later
        self.assertIsNone(emp.reporting_manager)
        self.assertTrue(LeaveLedger.objects.filter(employee=emp, kind="entitlement").exists())
        self.assertTrue(AuditEvent.objects.filter(module="employees", action="create",
                                                  subject_employee_id=emp.pk).exists())

    def test_numbers_are_sequential_with_a_separate_labor_series(self):
        self.assertEqual(self.make().employee_no, "C1-0001")
        self.assertEqual(self.make().employee_no, "C1-0002")
        self.assertEqual(self.make(worker_type="labor").employee_no, "C1-L0001")

    def test_numbers_already_in_use_are_skipped(self):
        for no in ("C1-0001", "C1-0002"):
            Employee.objects.create(company=self.co, employee_no=no, first_name="Old",
                                    joining_date=date(2019, 1, 1))
        self.assertEqual(self.make().employee_no, "C1-0003")

    def test_prefix_digits_and_start_number_are_configurable(self):
        EmployeeNumberSequence.objects.create(company=self.co, worker_type="staff",
                                              prefix="IV-", padding=5, next_number=1500)
        self.assertEqual(self.make().employee_no, "IV-01500")
        self.assertEqual(self.make().employee_no, "IV-01501")

    def test_failed_creation_does_not_burn_a_number(self):
        User.objects.create_user("c1-0001")                  # makes the default username clash
        r = self.client.post(self.create_url, self.payload(create_login="on"))
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Employee.objects.filter(employee_no__startswith="C1-").exists())
        User.objects.filter(username="c1-0001").delete()
        self.assertEqual(self.make().employee_no, "C1-0001")

    def test_saving_an_employee_without_a_number_assigns_one(self):
        emp = Employee(company=self.co, first_name="Direct", joining_date=date(2024, 1, 1))
        emp.save()
        self.assertEqual(emp.employee_no, "C1-0001")

    def test_next_number_preview(self):
        url = reverse("web:employee_next_number")
        ask = lambda company, wt: self.client.get(url, {"company": company, "worker_type": wt})
        self.assertContains(ask(self.co.pk, "staff"), "C1-0001")
        self.make()
        self.assertContains(ask(self.co.pk, "staff"), "C1-0002")
        self.assertContains(ask(self.co.pk, "labor"), "C1-L0001")
        other = Company.objects.create(code="C2", name="Company Two")
        self.assertContains(ask(other.pk, "staff"), "Choose a company")      # no access to that company
        self.assertFalse(EmployeeNumberSequence.objects.filter(company=other).exists())   # peek creates nothing

    def test_existing_staff_get_full_entitlement(self):
        emp = self.make(joining_date="2021-01-01")
        row = LeaveLedger.objects.get(employee=emp, leave_type=self.annual, kind="entitlement")
        self.assertEqual(row.days, Decimal("30.0"))

    def test_hr_cannot_see_or_create_in_other_companies(self):
        other = Company.objects.create(code="C2", name="Company Two")
        stranger = Employee.objects.create(company=other, employee_no="X1", first_name="Zed",
                                           joining_date=date(2022, 1, 1))
        self.assertEqual(self.client.get(reverse("web:employee_detail", args=[stranger.pk])).status_code, 404)
        self.assertNotContains(self.client.get(reverse("web:employee_list")), "Zed")
        r = self.client.post(self.create_url, self.payload(company=other.pk))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Employee.objects.filter(company=other).count(), 1)

    def test_create_with_login_shows_password_once_and_forces_change(self):
        emp = self.make(create_login="on")
        self.assertEqual(emp.user.username, "c1-0001")
        self.assertTrue(emp.user.must_change_password)
        password = self.client.session["new_login"]["password"]
        detail = reverse("web:employee_detail", args=[emp.pk])
        self.assertContains(self.client.get(detail), password)
        self.assertNotContains(self.client.get(detail), password)

    def test_must_change_password_is_enforced_then_cleared(self):
        self.emp_user.set_password("Old-Pass#2468")
        self.emp_user.must_change_password = True
        self.emp_user.save()
        self.client.force_login(self.emp_user)
        self.assertRedirects(self.client.get(reverse("web:dashboard")), reverse("web:password_change"))
        r = self.client.post(reverse("web:password_change"), {
            "old_password": "Old-Pass#2468", "new_password1": "Bright-New#7391", "new_password2": "Bright-New#7391"})
        self.assertRedirects(r, reverse("web:dashboard"))
        self.emp_user.refresh_from_db()
        self.assertFalse(self.emp_user.must_change_password)

    def test_assignment_change_writes_history_and_blocks_circular_reporting(self):
        emp = self.make(joining_date="2021-01-01")
        finance = Department.objects.create(company=self.co, code="FIN", name="Finance")
        url = reverse("web:employee_assign", args=[emp.pk])
        self.client.post(url, {"effective_from": timezone.localdate().isoformat(), "reason": "transfer",
                               "employment_type": "permanent", "department": finance.pk,
                               "designation": self.desig.pk, "reporting_manager": self.mgr.pk})
        emp.refresh_from_db()
        self.assertEqual(emp.department, finance)
        self.assertEqual(emp.history.count(), 2)
        self.assertEqual(emp.history.get(effective_to__isnull=True).department, finance)

        r = self.client.post(reverse("web:employee_assign", args=[self.mgr.pk]), {
            "effective_from": timezone.localdate().isoformat(), "reason": "manager_change",
            "employment_type": "permanent", "department": self.dept.pk,
            "designation": self.desig.pk, "reporting_manager": emp.pk})
        self.assertContains(r, "circular")
        self.mgr.refresh_from_db()
        self.assertIsNone(self.mgr.reporting_manager)

    def test_changing_whatsapp_number_resets_verification(self):
        emp = self.make(whatsapp_number="+973 3336 6235")
        self.assertEqual(emp.whatsapp_number, "97333366235")
        Employee.objects.filter(pk=emp.pk).update(whatsapp_verified=True)
        url = reverse("web:employee_edit", args=[emp.pk])
        base = {"first_name": "Sara", "last_name": "Ali"}
        self.client.post(url, {**base, "whatsapp_number": "+973 3336 6235"})
        emp.refresh_from_db()
        self.assertTrue(emp.whatsapp_verified)               # same number, different formatting
        self.client.post(url, {**base, "whatsapp_number": "97333111222"})
        emp.refresh_from_db()
        self.assertFalse(emp.whatsapp_verified)

    def test_list_search_htmx_partial_and_company_fields(self):
        self.make()
        r = self.client.get(reverse("web:employee_list"), {"q": "sara"}, HTTP_HX_REQUEST="true")
        self.assertContains(r, "C1-0001")
        self.assertNotContains(r, "<html")
        r = self.client.get(reverse("web:employee_company_fields"), {"company": self.co.pk})
        self.assertContains(r, "Operations")

    def test_register_with_the_minimum_fields(self):
        r = self.client.post(self.create_url, {
            "company": self.co.pk, "worker_type": "labor", "employment_type": "contract",
            "joining_date": timezone.localdate().isoformat(), "first_name": "Ravi",
            "designation": self.desig.pk})
        emp = Employee.objects.get(first_name="Ravi")
        self.assertRedirects(r, reverse("web:employee_detail", args=[emp.pk]))
        self.assertIsNone(emp.department)
        self.assertIsNone(emp.reporting_manager)
        self.assertEqual((emp.email, emp.last_name), ("", ""))
        self.assertEqual(emp.history.count(), 1)

    def test_designation_is_still_required(self):
        data = self.payload()
        data.pop("designation")
        self.assertEqual(self.client.post(self.create_url, data).status_code, 200)
        self.assertFalse(Employee.objects.filter(first_name="Sara").exists())

    def test_username_only_applies_when_a_login_is_requested(self):
        self.assertIsNone(self.make(username="custom.name").user)       # no login ticked: ignored
        emp = self.make(create_login="on", username="Sara.Ali", first_name="Sara2")
        self.assertEqual(emp.user.username, "Sara.Ali")

    def test_taken_or_invalid_username_is_a_field_error(self):
        User.objects.create_user("taken")
        for name in ("taken", "TAKEN", "bad name!"):
            r = self.client.post(self.create_url, self.payload(create_login="on", username=name))
            self.assertEqual(r.status_code, 200)
            self.assertContains(r, "is-invalid")
        self.assertFalse(Employee.objects.filter(first_name="Sara").exists())

    def test_employee_without_a_manager_gets_a_clear_message_on_leave(self):
        emp = self.make(create_login="on")
        emp.user.must_change_password = False
        emp.user.save()
        self.client.force_login(emp.user)
        sick = LeaveType.objects.get(company=self.co, code="SICK")
        r = self.client.post(reverse("web:leave_apply"), {
            "leave_type": sick.pk, "start_date": self.start.isoformat(), "end_date": self.start.isoformat()})
        self.assertContains(r, "Ask HR to assign a reporting manager")
        self.assertFalse(LeaveRequest.objects.filter(employee=emp).exists())
    def test_login_section_is_a_switch_that_starts_with_the_fields_in_one_group(self):
        page = self.client.get(self.create_url)
        self.assertContains(page, 'role="switch"')
        self.assertContains(page, '<fieldset id="login-fields"')
    def test_form_has_a_pinned_action_bar(self):
        self.assertContains(self.client.get(self.create_url), 'class="form-actions"')