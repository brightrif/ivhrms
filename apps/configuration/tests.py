from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import TestCase

from apps.audit.models import AuditEvent
from apps.organization.models import Company

from . import access, registry
from . import services as svc
from .models import SettingValue
from .registry import Capability, Module, Section, Setting

D = Decimal
User = get_user_model()


class TestModule:
    """Registers a throwaway module for the length of a test, so nothing depends on what real modules exist."""

    def __init__(self, test, **over):
        self.module = Module(key="demo", label="Demo", order=999, sections=[Section("All", [
            Setting("demo.flag", "A switch", "bool", False, per_company=True),
            Setting("demo.hours", "Hours", "decimal", D("8"), minimum=D("1"), maximum=D("24"), per_company=True),
            Setting("demo.count", "A count", "int", 3, minimum=D("0"), maximum=D("10")),
            Setting("demo.mode", "Mode", "choice", "week", choices=(("day", "Day"), ("week", "Week"))),
        ])], capabilities=[Capability("Do the demo thing", "auth.add_group", "just a hint")], **over)
        registry.register(self.module)
        test.addCleanup(registry.unregister, "demo")


class RegistryTests(TestCase):
    def test_a_module_must_follow_the_naming_rules(self):
        with self.assertRaises(ValueError):
            Setting("nodot", "x", "bool", False)
        with self.assertRaises(ValueError):
            Setting("a.b", "x", "weird", False)
        with self.assertRaises(ValueError):
            Setting("a.b", "x", "choice", "q")
        with self.assertRaises(ValueError):
            registry.register(Module(key="m1", label="M", sections=[Section("s", [Setting("other.x", "x", "bool", False)])]))

    def test_the_same_key_cannot_be_claimed_by_two_modules(self):
        TestModule(self)
        self.addCleanup(registry.unregister, "demo2")
        with self.assertRaises(ValueError):
            registry.register(Module(key="demo2", label="D2", sections=[Section("s", [Setting("demo.flag", "x", "bool", False)])]))

    def test_modules_are_listed_in_order_and_labor_describes_itself(self):
        TestModule(self)
        keys = [m.key for m in registry.modules()]
        self.assertIn("labor", keys)                               # found by settings_spec.py, nobody listed it here
        self.assertEqual(keys[-1], "demo")                         # order 999 sorts last


class ServiceTests(TestCase):
    def setUp(self):
        TestModule(self)
        self.co = Company.objects.create(code="IV1", name="IV One")
        self.other = Company.objects.create(code="IV2", name="IV Two")

    def test_unchosen_settings_use_the_built_in_default(self):
        self.assertEqual(svc.resolve("demo.hours"), (D("8"), "built-in"))
        self.assertEqual(svc.get("demo.mode"), "week")

    def test_a_default_applies_to_everyone_and_a_company_override_to_that_company_only(self):
        svc.set_value("demo.hours", "9")
        svc.set_value("demo.hours", "10", self.co)
        self.assertEqual(svc.resolve("demo.hours"), (D("9"), "default"))
        self.assertEqual(svc.resolve("demo.hours", self.co), (D("10"), "company"))
        self.assertEqual(svc.resolve("demo.hours", self.other), (D("9"), "default"))
        svc.use_default("demo.hours", self.co)
        self.assertEqual(svc.resolve("demo.hours", self.co), (D("9"), "default"))

    def test_a_setting_that_is_not_per_company_cannot_be_overridden_for_one(self):
        with self.assertRaisesMessage(svc.SettingError, "applies to all companies"):
            svc.set_value("demo.count", 5, self.co)
        svc.set_value("demo.count", 5)
        self.assertEqual(svc.get("demo.count", self.co), 5)            # a company asking still gets the default

    def test_values_are_checked_and_converted(self):
        for bad in ("abc", "0.5", "25", "nan"):
            with self.assertRaises(svc.SettingError, msg=bad):
                svc.set_value("demo.hours", bad)
        with self.assertRaisesMessage(svc.SettingError, "whole number"):
            svc.set_value("demo.count", "2.5")
        with self.assertRaisesMessage(svc.SettingError, "at most"):
            svc.set_value("demo.count", 11)
        with self.assertRaisesMessage(svc.SettingError, "listed options"):
            svc.set_value("demo.mode", "year")
        with self.assertRaisesMessage(svc.SettingError, "yes or no"):
            svc.set_value("demo.flag", "maybe")
        self.assertFalse(SettingValue.objects.exists())                # a refused value stores nothing
        svc.set_value("demo.hours", " 7.5 ")
        svc.set_value("demo.flag", "on")
        svc.set_value("demo.count", "4")
        self.assertEqual((svc.get("demo.hours"), svc.get("demo.flag"), svc.get("demo.count")), (D("7.5"), True, 4))

    def test_setting_the_same_value_twice_keeps_one_row(self):
        svc.set_value("demo.mode", "day")
        svc.set_value("demo.mode", "day")
        svc.set_value("demo.mode", "week")
        self.assertEqual(SettingValue.objects.filter(key="demo.mode").count(), 1)
        self.assertEqual(svc.get("demo.mode"), "week")

    def test_an_unknown_key_is_an_error_not_a_silent_default(self):
        with self.assertRaises(KeyError):
            svc.get("demo.nope")
        with self.assertRaises(KeyError):
            svc.set_value("demo.nope", 1)

    def test_changes_are_audited(self):
        svc.set_value("demo.mode", "day")
        svc.set_value("demo.mode", "week")
        events = AuditEvent.objects.filter(module="configuration")
        self.assertEqual(events.filter(action="create").count(), 1)
        self.assertEqual(events.filter(action="update").count(), 1)
        svc.set_value("demo.hours", "9", self.co)
        self.assertTrue(events.filter(company_id=self.co.pk).exists())


class PermissionTests(TestCase):
    def test_each_module_gets_a_view_and_a_change_right_that_nobody_has_by_default(self):
        for key in ("view_settings_labor", "edit_settings_labor"):
            perm = Permission.objects.get(content_type__app_label="configuration", codename=key)
            self.assertFalse(Group.objects.filter(permissions=perm).exists(), key)


class AccessGridTests(TestCase):
    def setUp(self):
        TestModule(self)
        self.root = User.objects.create_user("root", is_superuser=True)
        self.hr = Group.objects.get(name="HR")
        self.fin = Group.objects.get(name="Finance")
        self.add_group = Permission.objects.get(content_type__app_label="auth", codename="add_group")
        self.labor_prepare = Permission.objects.get(content_type__app_label="labor", codename="add_overtimeclaim")

    def test_the_grid_shows_what_groups_have_now(self):
        groups, rows = access.grid()
        prepare = next(r for r in rows if r.perm == "labor.add_overtimeclaim")
        self.assertIn(self.hr.pk, prepare.granted)                    # HR was given this by the labor defaults
        self.assertNotIn(self.fin.pk, prepare.granted)
        self.assertIn("labor.view_overtimeclaim", [r.perm for r in rows])
        self.assertIn("configuration.edit_settings_labor", [r.perm for r in rows])
        self.assertTrue(any(r.perm == "auth.add_group" and r.hint == "just a hint" for r in rows))

    def test_applying_gives_and_takes_only_what_the_superuser_chose(self):
        before_hr = set(self.hr.permissions.values_list("pk", flat=True))
        _, rows = access.grid()
        wanted = {(r.perm, g) for r in rows for g in r.granted}              # start from exactly what exists
        wanted.add(("configuration.edit_settings_labor", self.fin.pk))        # give Finance one right
        wanted.discard(("labor.add_overtimeclaim", self.hr.pk))               # take one from HR
        granted, revoked = access.apply(self.root, wanted)
        self.assertEqual((granted, revoked), (1, 1))
        self.assertTrue(self.fin.permissions.filter(codename="edit_settings_labor").exists())
        self.assertFalse(self.hr.permissions.filter(pk=self.labor_prepare.pk).exists())
        changed = before_hr ^ set(self.hr.permissions.values_list("pk", flat=True))
        self.assertEqual(changed, {self.labor_prepare.pk})                   # nothing else about HR was touched

    def test_a_right_that_is_not_in_the_registry_is_never_changed(self):
        stray = Permission.objects.get(content_type__app_label="auth", codename="delete_group")
        self.hr.permissions.add(stray)
        access.apply(self.root, set())                                        # "nobody should have anything"
        self.assertTrue(self.hr.permissions.filter(pk=stray.pk).exists())

    def test_every_change_is_logged_with_who_and_what(self):
        access.apply(self.root, {("configuration.view_settings_labor", self.fin.pk)})
        reasons = list(AuditEvent.objects.filter(module="configuration", object_repr="Finance").values_list("reason", flat=True))
        self.assertTrue(any("root gave group 'Finance'" in r and "configuration.view_settings_labor" in r for r in reasons))
        access.apply(self.root, set())
        self.assertTrue(AuditEvent.objects.filter(module="configuration", reason__contains="took from group 'Finance'").exists())

    def test_applying_twice_changes_nothing_the_second_time(self):
        wanted = {("configuration.view_settings_labor", self.fin.pk)}
        access.apply(self.root, wanted)
        self.assertEqual(access.apply(self.root, wanted)[0], 0)
