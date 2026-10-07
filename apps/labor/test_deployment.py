from datetime import date
from decimal import Decimal

from django.db import IntegrityError, transaction

from apps.audit.models import AuditEvent
from apps.employees.models import Employee
from apps.organization.models import Location, Project

from . import deployment, services
from .allocation import LaborAllocation, WorkOrder
from .models import Contractor
from .tests import LaborCase

D = Decimal
MAR1, MAR5, MAR10 = date(2026, 3, 1), date(2026, 3, 5), date(2026, 3, 10)


class DeploymentCase(LaborCase):
    def setUp(self):
        super().setUp()
        self.site1 = Location.objects.create(code="S1", name="Site One", is_site=True)
        self.site2 = Location.objects.create(code="S2", name="Site Two", is_site=True)
        self.office = Location.objects.create(code="HQ", name="Head Office", is_site=False)
        self.p1 = Project.objects.create(company=self.co, code="P1", name="Tower A", location=self.site1)
        self.p2 = Project.objects.create(company=self.co, code="P2", name="Villa B", location=self.site2)
        self.worker1 = self.make_worker()
        self.emp = self.worker1.employee

    def place(self, employee=None, project=None, location=None, work_order=None, on=MAR1):
        return deployment.allocate(employee or self.emp, project=project or self.p1, location=location or self.site1,
                                   work_order=work_order, effective_from=on)

    def make_other(self, name="Imran", **kw):
        e = Employee(company=self.co, first_name=name, joining_date=date(2026, 1, 1))
        return services.create_labor_worker(e, engagement="direct", trade=self.mason, wage_basis="daily",
                                            rate=D("5"), **kw).employee


class AllocateTests(DeploymentCase):
    def test_first_allocation_is_open_and_remembers_the_company(self):
        a = self.place()
        self.assertIsNone(a.effective_to)
        self.assertEqual((a.company_id, a.project, a.location), (self.co.pk, self.p1, self.site1))
        self.assertEqual(deployment.open_allocation(self.emp), a)

    def test_a_move_closes_the_old_allocation_the_day_before(self):
        old = self.place()
        new = self.place(project=self.p2, location=self.site2, on=MAR10)
        old.refresh_from_db()
        self.assertEqual(old.effective_to, date(2026, 3, 9))
        self.assertIsNone(new.effective_to)
        self.assertEqual(LaborAllocation.objects.filter(employee=self.emp, effective_to__isnull=True).count(), 1)

    def test_same_place_twice_is_refused(self):
        self.place()
        with self.assertRaisesMessage(services.LaborError, "already allocated there"):
            self.place(on=MAR10)

    def test_a_move_must_start_after_the_current_allocation(self):
        self.place(on=MAR5)
        for day in (MAR5, MAR1):
            with self.assertRaisesMessage(services.LaborError, "must start after"):
                self.place(project=self.p2, location=self.site2, on=day)
        self.assertEqual(LaborAllocation.objects.count(), 1)

    def test_cannot_start_before_the_worker_joined(self):
        with self.assertRaisesMessage(services.LaborError, "joined on 01 Jan 2026"):
            self.place(on=date(2025, 12, 31))

    def test_destination_rules(self):
        other_project = Project.objects.create(company=self.other, code="X1", name="Elsewhere", location=self.site1)
        with self.assertRaisesMessage(services.LaborError, "another company"):
            self.place(project=other_project)
        self.p1.status = "on_hold"
        self.p1.save()
        with self.assertRaisesMessage(services.LaborError, "not active"):
            self.place()
        with self.assertRaisesMessage(services.LaborError, "not an active work site"):
            self.place(project=self.p2, location=self.office)
        self.assertEqual(LaborAllocation.objects.count(), 0)

    def test_work_order_rules(self):
        wo1 = WorkOrder.objects.create(project=self.p1, code="WO-1", name="Foundations")
        wo2 = WorkOrder.objects.create(project=self.p2, code="WO-2", name="Roof")
        with self.assertRaisesMessage(services.LaborError, "another project"):
            self.place(work_order=wo2)
        wo1.status = "closed"
        wo1.save()
        with self.assertRaisesMessage(services.LaborError, "is closed"):
            self.place(work_order=wo1)
        wo1.status = "open"
        wo1.save()
        self.assertEqual(self.place(work_order=wo1).work_order, wo1)

    def test_changing_only_the_work_order_is_a_move(self):
        wo1 = WorkOrder.objects.create(project=self.p1, code="WO-1", name="Foundations")
        wo3 = WorkOrder.objects.create(project=self.p1, code="WO-3", name="Slab")
        self.place(work_order=wo1)
        self.place(work_order=wo3, on=MAR10)
        self.assertEqual(deployment.open_allocation(self.emp).work_order, wo3)

    def test_workers_without_a_profile_or_who_left_cannot_be_placed(self):
        no_profile = Employee.objects.create(company=self.co, first_name="Old", worker_type="labor",
                                             joining_date=date(2025, 1, 1))
        with self.assertRaisesMessage(services.LaborError, "no labor profile"):
            self.place(employee=no_profile)
        Employee.objects.filter(pk=self.emp.pk).update(status="separated")
        self.emp.refresh_from_db()
        with self.assertRaisesMessage(services.LaborError, "has left"):
            self.place()

    def test_database_allows_only_one_open_allocation(self):
        self.place()
        with self.assertRaises(IntegrityError), transaction.atomic():
            LaborAllocation.objects.create(employee=self.emp, project=self.p2, location=self.site2, effective_from=MAR10)


class ReleaseTests(DeploymentCase):
    def test_release_sets_the_last_day_and_leaves_the_worker_unallocated(self):
        self.place()
        a = deployment.release(self.emp, last_day=MAR10)
        self.assertEqual(a.effective_to, MAR10)
        self.assertIsNone(deployment.open_allocation(self.emp))
        self.assertEqual(deployment.unallocated_profiles(self._admin()).count(), 1)

    def test_release_rules(self):
        with self.assertRaisesMessage(services.LaborError, "not allocated anywhere"):
            deployment.release(self.emp, last_day=MAR10)
        self.place(on=MAR5)
        with self.assertRaisesMessage(services.LaborError, "cannot be before"):
            deployment.release(self.emp, last_day=MAR1)

    def test_allocating_again_must_start_after_the_last_stint_ended(self):
        self.place()
        deployment.release(self.emp, last_day=MAR10)
        with self.assertRaisesMessage(services.LaborError, "ran until 10 Mar 2026"):
            self.place(on=MAR10)
        self.assertIsNotNone(self.place(on=date(2026, 3, 11)))

    def _admin(self):
        from django.contrib.auth import get_user_model
        return get_user_model().objects.create_superuser("root", password="x")


class TransferTests(DeploymentCase):
    def test_moves_a_crew_and_skips_those_already_there(self):
        b = self.make_other("Imran")
        c = self.make_other("Salim")
        self.place(employee=self.emp, project=self.p2, location=self.site2)       # already at the destination
        self.place(employee=b, project=self.p1, location=self.site1)
        moved, already = deployment.transfer([self.emp, b, c], project=self.p2, location=self.site2, effective_from=MAR10)
        self.assertEqual((moved, already), (2, 1))
        for e in (self.emp, b, c):
            self.assertEqual(deployment.open_allocation(e).project, self.p2)

    def test_all_or_nothing(self):
        b = self.make_other("Imran")
        left = self.make_other("Gone")
        Employee.objects.filter(pk=left.pk).update(status="separated")
        left.refresh_from_db()
        self.place(employee=b, project=self.p1, location=self.site1, on=MAR5)
        with self.assertRaises(services.LaborError) as ctx:
            deployment.transfer([self.emp, b, left], project=self.p2, location=self.site2, effective_from=MAR1)
        text = str(ctx.exception)
        self.assertIn("Nobody was moved", text)
        self.assertIn("Gone", text)                          # the one who left
        self.assertIn("Imran", text)                         # the one who is already there from a later date
        self.assertEqual(LaborAllocation.objects.filter(project=self.p2).count(), 0)
        self.assertIsNone(deployment.open_allocation(self.emp))

    def test_a_bad_destination_refuses_everyone(self):
        with self.assertRaisesMessage(services.LaborError, "not an active work site"):
            deployment.transfer([self.emp], project=self.p2, location=self.office, effective_from=MAR1)


class LookupTests(DeploymentCase):
    def test_allocation_on_a_date_gives_where_the_worker_was_then(self):
        self.place(on=MAR1)
        self.place(project=self.p2, location=self.site2, on=MAR10)
        on = lambda d: deployment.allocation_on(self.emp, d)
        self.assertIsNone(on(date(2026, 2, 28)))
        self.assertEqual(on(date(2026, 3, 9)).project, self.p1)
        self.assertEqual(on(MAR10).project, self.p2)
        self.assertEqual(on(date(2026, 12, 31)).project, self.p2)

    def test_site_overview_counts_direct_and_contracted_and_lists_empty_projects(self):
        contracted = services.create_labor_worker(
            Employee(company=self.co, first_name="Imran", joining_date=date(2026, 1, 1)), engagement="contracted",
            contractor=self.contractor, trade=self.mason, wage_basis="daily", rate=D("5")).employee
        self.place()
        self.place(employee=contracted)
        from django.contrib.auth import get_user_model
        root = get_user_model().objects.create_superuser("root", password="x")
        rows = {r["project"].code: r for r in deployment.site_overview(root)}
        self.assertEqual((rows["P1"]["total"], rows["P1"]["direct"], rows["P1"]["contracted"]), (2, 1, 1))
        self.assertEqual(rows["P2"]["total"], 0)                       # an active project nobody is on yet

    def test_overview_ignores_people_who_left(self):
        self.place()
        Employee.objects.filter(pk=self.emp.pk).update(status="separated")
        from django.contrib.auth import get_user_model
        root = get_user_model().objects.create_superuser("root", password="x")
        self.assertEqual({r["project"].code: r["total"] for r in deployment.site_overview(root)}["P1"], 0)


class WorkOrderTests(DeploymentCase):
    def test_cannot_close_while_workers_are_on_it_and_can_after(self):
        wo = WorkOrder.objects.create(project=self.p1, code="WO-1", name="Foundations")
        self.place(work_order=wo)
        with self.assertRaisesMessage(services.LaborError, "still allocated to WO-1"):
            deployment.set_work_order_open(wo, False)
        deployment.release(self.emp, last_day=MAR10)
        deployment.set_work_order_open(wo, False)
        wo.refresh_from_db()
        self.assertEqual(wo.status, "closed")
        deployment.set_work_order_open(wo, True)
        wo.refresh_from_db()
        self.assertEqual(wo.status, "open")

    def test_code_is_unique_within_a_project_only(self):
        WorkOrder.objects.create(project=self.p1, code="WO-1", name="A")
        WorkOrder.objects.create(project=self.p2, code="WO-1", name="B")
        with self.assertRaises(IntegrityError), transaction.atomic():
            WorkOrder.objects.create(project=self.p1, code="WO-1", name="C")

    def test_work_order_takes_its_projects_company(self):
        self.assertEqual(WorkOrder.objects.create(project=self.p1, code="WO-9", name="X").company_id, self.co.pk)


class AuditTests(DeploymentCase):
    def test_every_allocation_change_is_logged_against_the_worker(self):
        events = lambda action: AuditEvent.objects.filter(module="labor", subject_employee_id=self.emp.pk, action=action)
        before = {a: events(a).count() for a in ("create", "update")}
        self.place()
        self.place(project=self.p2, location=self.site2, on=MAR10)
        deployment.release(self.emp, last_day=date(2026, 4, 1))
        self.assertEqual(events("create").count() - before["create"], 2)       # two allocations
        self.assertEqual(events("update").count() - before["update"], 2)       # the move closed one, the release another
        self.assertTrue(all(e.company_id == self.co.pk for e in events("create")))
