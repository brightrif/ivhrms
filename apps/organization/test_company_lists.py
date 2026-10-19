from datetime import date

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import RequestFactory, TestCase

from apps.airtickets.models import TicketPolicy
from apps.audit.context import reset_request, set_request
from apps.employees.models import Employee
from apps.organization.company_lists import split_shared
from apps.organization.models import Company, Designation, Grade
from apps.web.forms.staff import AssignmentForm

User = get_user_model()


class ListCase(TestCase):
    def setUp(self):
        self.spare = Company.objects.create(code="ISP", name="IV Spare Parts")
        self.build = Company.objects.create(code="IVC", name="IV Construction")
        self.user = User.objects.create_user("someone")

    def working_in(self, company):
        """Run as a login working in `company` in the Separate view, the way the middleware sets a request up."""
        request = RequestFactory().get("/")
        request.user = self.user
        request.company_scope = company.pk
        token = set_request(request)
        self.addCleanup(reset_request, token)

    def person(self, company, number, **kw):
        return Employee.objects.create(company=company, employee_no=number, first_name=number,
                                       joining_date=date(2020, 1, 1), **kw)


class VisibilityTests(ListCase):
    def setUp(self):
        super().setUp()
        self.shared = Designation.objects.create(name="Manager")
        self.mine = Designation.objects.create(name="Parts Clerk", company=self.spare)
        self.theirs = Designation.objects.create(name="Site Engineer", company=self.build)

    def test_outside_the_separate_view_every_row_shows(self):
        self.assertEqual(Designation.objects.count(), 3)

    def test_the_separate_view_shows_shared_rows_and_the_companys_own(self):
        self.working_in(self.spare)
        self.assertEqual(set(Designation.objects.values_list("name", flat=True)), {"Manager", "Parts Clerk"})

    def test_for_company_gives_its_own_and_the_shared_ones(self):
        names = set(Designation.objects.for_company(self.build).values_list("name", flat=True))
        self.assertEqual(names, {"Manager", "Site Engineer"})

    def test_a_row_made_in_the_separate_view_belongs_to_that_company(self):
        self.working_in(self.build)
        made = Designation.objects.create(name="Foreman")
        self.assertEqual(made.company, self.build)

    def test_a_row_made_outside_it_is_shared(self):
        self.assertIsNone(Designation.objects.create(name="Driver").company)

    def test_two_companies_may_each_have_a_designation_with_the_same_name(self):
        Designation.objects.create(name="Accountant", company=self.spare)
        Designation.objects.create(name="Accountant", company=self.build)

    def test_a_grade_code_may_repeat_between_companies_but_not_inside_one(self):
        Grade.objects.create(code="G1", name="Grade 1", company=self.spare)
        Grade.objects.create(code="G1", name="Grade 1", company=self.build)
        with self.assertRaises(ValidationError):
            Grade(code="g1", name="Again", company=self.spare).clean()
        with self.assertRaises(ValidationError):
            Grade(code="G1", name="Shared clash").clean()           # shared would clash with both

    def test_the_assignment_form_offers_only_this_companys_designations_and_grades(self):
        Grade.objects.create(code="S1", name="Spare 1", company=self.spare)
        Grade.objects.create(code="C1", name="Construction 1", company=self.build)
        form = AssignmentForm(employee=self.person(self.build, "C-1"))
        self.assertEqual(set(form.fields["designation"].queryset.values_list("name", flat=True)),
                         {"Manager", "Site Engineer"})
        self.assertEqual(set(form.fields["grade"].queryset.values_list("code", flat=True)), {"C1"})


class SplitTests(ListCase):
    def setUp(self):
        super().setUp()
        self.manager = Designation.objects.create(name="Manager")
        self.grade = Grade.objects.create(code="G1", name="Grade 1", rank=3)
        self.a = self.person(self.spare, "S-1", designation=self.manager, grade=self.grade)
        self.b = self.person(self.build, "C-1", designation=self.manager, grade=self.grade)
        self.policy = TicketPolicy.objects.create(company=self.build, name="Senior", min_grade=self.grade)

    def test_a_preview_changes_nothing(self):
        report = split_shared(apply=False)
        self.assertEqual(len(report), 2)
        self.assertEqual(Designation.objects.filter(company__isnull=True).count(), 1)
        self.assertEqual(Designation.objects.count(), 1)

    def test_each_company_gets_its_own_copy_and_its_people_move_onto_it(self):
        report = split_shared(apply=True)
        self.assertEqual({r["kind"] for r in report}, {"Designation", "Grade"})
        self.assertFalse(Designation.objects.filter(company__isnull=True).exists())
        self.assertFalse(Grade.objects.filter(company__isnull=True).exists())
        for emp in (self.a, self.b):
            emp.refresh_from_db()
            self.assertEqual(emp.designation.company_id, emp.company_id)
            self.assertEqual(emp.grade.company_id, emp.company_id)
            self.assertEqual((emp.designation.name, emp.grade.code, emp.grade.rank), ("Manager", "G1", 3))
        self.policy.refresh_from_db()
        self.assertEqual(self.policy.min_grade.company_id, self.build.pk)
        self.assertNotEqual(self.a.designation_id, self.b.designation_id)

    def test_running_it_again_does_nothing(self):
        split_shared(apply=True)
        self.assertEqual(split_shared(apply=True), [])
