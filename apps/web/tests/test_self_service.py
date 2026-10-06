from datetime import timedelta

from django.urls import reverse

from apps.attendance.tests import BaseCase
from apps.audit.models import AuditEvent
from apps.leave.models import LeaveRequest


class WebTests(BaseCase):
    def test_login_required(self):
        r = self.client.get(reverse("web:dashboard"))
        self.assertEqual(r.status_code, 302)
        self.assertIn(reverse("web:login"), r["Location"])

    def test_pages_render_for_employee(self):
        self.client.force_login(self.emp_user)
        for name in ("web:dashboard", "web:leave_list", "web:leave_apply", "web:attendance", "web:approvals"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)

    def test_login_without_employee_record_gets_403(self):
        self.client.force_login(self.hr_user)           # HR user has no Employee link in this fixture
        self.assertEqual(self.client.get(reverse("web:dashboard")).status_code, 403)

    def test_leave_preview_counts_days(self):
        self.client.force_login(self.emp_user)
        r = self.client.post(reverse("web:leave_preview"), {
            "leave_type": self.annual.pk,
            "start_date": self.start.isoformat(),
            "end_date": (self.start + timedelta(days=1)).isoformat(),
        })
        self.assertContains(r, "<strong>2</strong>")

    def test_apply_then_two_step_approval_through_the_web_layer(self):
        self.client.force_login(self.emp_user)
        r = self.client.post(reverse("web:leave_apply"), {
            "leave_type": self.annual.pk,
            "start_date": self.start.isoformat(),
            "end_date": (self.start + timedelta(days=1)).isoformat(),
            "reason": "Family trip",
        })
        self.assertRedirects(r, reverse("web:leave_list"))
        leave = LeaveRequest.objects.get(employee=self.emp)
        url = reverse("web:approval_decide", args=[self.approval_for(leave).pk])

        # the requester cannot approve their own request, and the attempt is audited
        r = self.client.post(url, {"decision": "approve"})
        self.assertContains(r, "You cannot act on this request")
        self.assertTrue(AuditEvent.objects.filter(action="permission_denied", module="approvals").exists())

        self.client.force_login(self.mgr_user)
        self.assertContains(self.client.post(url, {"decision": "approve"}), "Forwarded")

        self.client.force_login(self.hr_user)
        self.assertContains(self.client.post(url, {"decision": "approve"}), "complete")
        leave.refresh_from_db()
        self.assertEqual(leave.status, "approved")

    def test_reject_requires_comment(self):
        self.client.force_login(self.emp_user)
        self.client.post(reverse("web:leave_apply"), {
            "leave_type": self.annual.pk, "start_date": self.start.isoformat(),
            "end_date": self.start.isoformat()})
        leave = LeaveRequest.objects.get(employee=self.emp)
        url = reverse("web:approval_decide", args=[self.approval_for(leave).pk])
        self.client.force_login(self.mgr_user)
        self.assertContains(self.client.post(url, {"decision": "reject", "comment": ""}),
                            "A comment is required")
        self.assertContains(self.client.post(url, {"decision": "reject", "comment": "Busy"}), "Rejected")

    def test_shell_menu_and_form_styling(self):
        self.client.force_login(self.emp_user)
        r = self.client.get(reverse("web:leave_apply"))
        self.assertContains(r, "bootstrap.min.css")
        self.assertContains(r, 'class="form-select"')       # the leave type dropdown
        self.assertContains(r, "nav-link active")           # the Leave menu item is highlighted

    def test_login_page_shows_logo(self):
        r = self.client.get(reverse("web:login"))
        self.assertContains(r, "web/img/logo.png")