from datetime import date

from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from apps.accounts import user_admin
from apps.accounts.models import User
from apps.configuration import access
from apps.employees.models import Employee
from apps.organization.models import Company, CompanyAccess


class UserAdminCase(TestCase):
    def setUp(self):
        self.co = Company.objects.create(code="C1", name="Company One")
        self.co2 = Company.objects.create(code="C2", name="Company Two")
        self.hr_group, _ = Group.objects.get_or_create(name="HR")
        self.root = User.objects.create_superuser("root", password="pw")
        self.plain = User.objects.create_user("plain")                 # a login with no group and no company
        self.client.force_login(self.root)


class ServiceTests(UserAdminCase):
    def test_a_new_login_gets_a_temporary_password_groups_and_companies(self):
        user, creds = user_admin.create_user(self.root, username="hr1", first_name="Hana", groups=[self.hr_group],
                                             companies=[self.co])
        self.assertTrue(user.must_change_password)
        self.assertTrue(user.check_password(creds["password"]))
        self.assertEqual(list(user.groups.all()), [self.hr_group])
        self.assertEqual(list(CompanyAccess.objects.filter(user=user).values_list("company", flat=True)), [self.co.pk])

    def test_usernames_are_unique_ignoring_case(self):
        with self.assertRaisesMessage(user_admin.UserAdminError, "already taken"):
            user_admin.create_user(self.root, username="PLAIN")
        with self.assertRaisesMessage(user_admin.UserAdminError, "username"):
            user_admin.create_user(self.root, username="  ")

    def test_access_follows_the_lists_and_says_what_changed(self):
        changes = user_admin.set_access(self.root, self.plain, groups=[self.hr_group], companies=[self.co, self.co2])
        self.assertEqual(len(changes), 3)
        changes = user_admin.set_access(self.root, self.plain, groups=[], companies=[self.co])
        self.assertEqual(len(changes), 2)
        self.assertFalse(self.plain.groups.exists())
        self.assertEqual(CompanyAccess.objects.filter(user=self.plain).count(), 1)
        self.assertEqual(user_admin.set_access(self.root, self.plain, groups=[], companies=[self.co]), [])

    def test_you_cannot_switch_yourself_off(self):
        with self.assertRaisesMessage(user_admin.UserAdminError, "your own"):
            user_admin.set_access(self.root, self.root, groups=[], companies=[], is_active=False)

    def test_the_last_active_superuser_cannot_be_switched_off(self):
        boss = User.objects.create_superuser("boss", password="pw")
        user_admin.set_access(self.root, boss, groups=[], companies=[], is_active=False)     # root stays active: fine
        with self.assertRaisesMessage(user_admin.UserAdminError, "only active superuser"):
            user_admin.set_access(boss, self.root, groups=[], companies=[], is_active=False)

    def test_a_reset_gives_a_new_password_and_forces_a_change(self):
        self.plain.set_password("old")
        self.plain.save()
        creds = user_admin.reset_password(self.root, self.plain)
        self.plain.refresh_from_db()
        self.assertTrue(self.plain.check_password(creds["password"]))
        self.assertFalse(self.plain.check_password("old"))
        self.assertTrue(self.plain.must_change_password)

    def test_the_hints_say_why_someone_sees_nothing(self):
        text = " ".join(t for _, t in user_admin.hints(self.plain))
        self.assertIn("In no group", text)
        self.assertIn("No company access", text)
        self.assertIn("Not linked to an employee", text)
        self.assertIn("everything", " ".join(t for _, t in user_admin.hints(self.root)))
        user_admin.set_access(self.root, self.plain, groups=[self.hr_group], companies=[self.co])
        text = " ".join(t for _, t in user_admin.hints(self.plain))
        self.assertNotIn("In no group", text)
        self.assertNotIn("No company access", text)

    def test_the_rights_listed_come_from_the_groups(self):
        row = next(r for r in access.rows() if access._permission(r.perm) is not None)
        group = Group.objects.create(name="Tester")
        group.permissions.add(access._permission(row.perm))
        self.assertEqual(user_admin.effective_rights(self.plain), [])
        self.plain.groups.add(group)
        user = User.objects.get(pk=self.plain.pk)                     # a fresh copy: permissions are cached per user
        found = dict(user_admin.effective_rights(user))
        self.assertIn(row.label, found[row.module.label])


class PageTests(UserAdminCase):
    def test_only_a_superuser_can_open_the_pages(self):
        self.client.force_login(self.plain)
        for name, args in (("settings_users", []), ("settings_user_add", []), ("settings_user", [self.plain.pk]),
                           ("settings_user_for_employee", [])):
            self.assertEqual(self.client.get(reverse(f"web:{name}", args=args)).status_code, 403, name)
        self.assertEqual(self.client.post(reverse("web:settings_user_reset", args=[self.plain.pk])).status_code, 403)

    def test_the_list_shows_everyone_and_who_has_nothing(self):
        resp = self.client.get(reverse("web:settings_users"))
        self.assertContains(resp, "plain")
        self.assertContains(resp, "Superuser")
        self.assertContains(self.client.get(reverse("web:settings_users") + "?status=no_group"), "plain")
        self.assertNotContains(self.client.get(reverse("web:settings_users") + "?status=no_group"), "root")

    def test_adding_a_login_shows_the_password_once(self):
        resp = self.client.post(reverse("web:settings_user_add"), {
            "username": "hr1", "first_name": "Hana", "last_name": "", "email": "",
            "groups": [self.hr_group.pk], "companies": [self.co.pk]})
        user = User.objects.get(username="hr1")
        self.assertRedirects(resp, reverse("web:settings_user", args=[user.pk]))
        first = self.client.get(reverse("web:settings_user", args=[user.pk]))
        self.assertContains(first, "Temporary password")
        self.assertNotContains(self.client.get(reverse("web:settings_user", args=[user.pk])), "Temporary password")

    def test_a_duplicate_username_is_refused_on_the_page(self):
        resp = self.client.post(reverse("web:settings_user_add"), {"username": "plain"})
        self.assertContains(resp, "already taken")
        self.assertEqual(User.objects.filter(username__iexact="plain").count(), 1)

    def test_setting_access_on_the_person_page(self):
        url = reverse("web:settings_user", args=[self.plain.pk])
        self.assertContains(self.client.get(url), "In no group")
        resp = self.client.post(url, {"groups": [self.hr_group.pk], "companies": [self.co.pk], "is_active": "on"})
        self.assertRedirects(resp, url)
        self.assertEqual(list(self.plain.groups.all()), [self.hr_group])
        self.assertNotContains(self.client.get(url), "In no group")
        self.client.post(url, {"groups": [self.hr_group.pk], "companies": [self.co.pk]})         # switch off
        self.plain.refresh_from_db()
        self.assertFalse(self.plain.is_active)

    def test_a_login_for_an_employee_is_linked_to_their_record(self):
        emp = Employee.objects.create(company=self.co, employee_no="E7", first_name="Lina",
                                      joining_date=date(2021, 1, 1))
        linked = User.objects.create_user("already")
        Employee.objects.create(company=self.co, employee_no="E8", first_name="Lola", user=linked,
                                joining_date=date(2021, 1, 1))
        url = reverse("web:settings_user_for_employee")
        self.assertContains(self.client.get(url + "?q=a"), "at least 2 letters")
        resp = self.client.get(url + "?q=Li")
        self.assertContains(resp, "Lina")
        self.assertNotContains(self.client.get(url + "?q=Lo"), "Lola")                            # has a login already
        resp = self.client.post(reverse("web:settings_user_create_login", args=[emp.pk]))
        emp.refresh_from_db()
        self.assertTrue(emp.user_id)
        self.assertRedirects(resp, reverse("web:settings_user", args=[emp.user_id]))
        self.assertContains(self.client.get(reverse("web:settings_user", args=[emp.user_id])), "Temporary password")

    def test_the_access_grid_links_to_the_people_in_each_group(self):
        self.plain.groups.add(self.hr_group)
        resp = self.client.get(reverse("web:settings_access"))
        self.assertContains(resp, f"?group={self.hr_group.pk}")
        self.assertContains(resp, "1 user")
        self.assertContains(self.client.get(reverse("web:settings_users") + f"?group={self.hr_group.pk}"), "plain")
