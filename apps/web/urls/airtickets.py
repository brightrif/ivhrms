from django.urls import path

from apps.web.views import airticket_reports as reports_views
from apps.web.views import airticket_requests as requests_views
from apps.web.views import airtickets

# Names start with "employee_" so the Staff entry in the sidebar stays highlighted on these pages.
urlpatterns = [
    path("staff/tickets/", airtickets.tickets_list, name="employee_tickets"),
    path("staff/tickets/reports/", reports_views.reports_index, name="employee_tickets_reports"),
    path("staff/tickets/reports/eligible/", reports_views.report_eligible, name="employee_tickets_report_eligible"),
    path("staff/tickets/reports/used/", reports_views.report_used, name="employee_tickets_report_used"),
    path("staff/tickets/reports/pending/", reports_views.report_pending, name="employee_tickets_report_pending"),
    path("staff/tickets/reports/expenses/", reports_views.report_expenses, name="employee_tickets_report_expenses"),
    path("staff/tickets/requests/", requests_views.requests_list, name="employee_tickets_requests"),
    path("staff/tickets/requests/<int:pk>/", requests_views.request_detail, name="employee_tickets_request"),
    path("staff/tickets/requests/<int:pk>/withdraw/", requests_views.request_withdraw,
         name="employee_tickets_request_withdraw"),
    path("staff/tickets/requests/<int:pk>/issue/", requests_views.request_issue, name="employee_tickets_request_issue"),
    path("staff/tickets/requests/<int:pk>/travelled/", requests_views.request_travelled,
         name="employee_tickets_request_travelled"),
    path("staff/tickets/requests/<int:pk>/paid/", requests_views.request_paid, name="employee_tickets_request_paid"),
    path("staff/tickets/requests/<int:pk>/cancel/", requests_views.request_cancel,
         name="employee_tickets_request_cancel"),
    path("staff/tickets/<int:pk>/request/new/", requests_views.request_add, name="employee_tickets_request_add"),
    # the employee's own page
    path("air-ticket/", requests_views.my_tickets, name="my_tickets"),
    path("air-ticket/request/", requests_views.my_ticket_apply, name="my_ticket_apply"),
    path("air-ticket/<int:pk>/withdraw/", requests_views.my_ticket_withdraw, name="my_ticket_withdraw"),
    path("staff/tickets/sync/", airtickets.tickets_sync, name="employee_tickets_sync"),
    path("staff/tickets/policies/", airtickets.policies, name="employee_tickets_policies"),
    path("staff/tickets/policies/new/", airtickets.policy_add, name="employee_tickets_policy_add"),
    path("staff/tickets/policies/<int:pk>/edit/", airtickets.policy_edit, name="employee_tickets_policy_edit"),
    path("staff/tickets/policies/<int:pk>/toggle/", airtickets.policy_toggle, name="employee_tickets_policy_toggle"),
    path("staff/tickets/<int:pk>/", airtickets.tickets_detail, name="employee_tickets_detail"),
    path("staff/tickets/<int:pk>/profile/", airtickets.tickets_profile, name="employee_tickets_profile"),
    path("staff/tickets/<int:pk>/past/", airtickets.tickets_past, name="employee_tickets_past"),
]
