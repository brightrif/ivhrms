from django.urls import path

from apps.web.views import airtickets

# Names start with "employee_" so the Staff entry in the sidebar stays highlighted on these pages.
urlpatterns = [
    path("staff/tickets/", airtickets.tickets_list, name="employee_tickets"),
    path("staff/tickets/sync/", airtickets.tickets_sync, name="employee_tickets_sync"),
    path("staff/tickets/policies/", airtickets.policies, name="employee_tickets_policies"),
    path("staff/tickets/policies/new/", airtickets.policy_add, name="employee_tickets_policy_add"),
    path("staff/tickets/policies/<int:pk>/edit/", airtickets.policy_edit, name="employee_tickets_policy_edit"),
    path("staff/tickets/policies/<int:pk>/toggle/", airtickets.policy_toggle, name="employee_tickets_policy_toggle"),
    path("staff/tickets/<int:pk>/", airtickets.tickets_detail, name="employee_tickets_detail"),
    path("staff/tickets/<int:pk>/profile/", airtickets.tickets_profile, name="employee_tickets_profile"),
    path("staff/tickets/<int:pk>/past/", airtickets.tickets_past, name="employee_tickets_past"),
]
