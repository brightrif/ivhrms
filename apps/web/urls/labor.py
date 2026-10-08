from django.urls import path

from apps.web.views import labor, labor_attendance, labor_overtime, labor_sites, labor_timesheet

urlpatterns = [
    path("labor/", labor.labor_list, name="labor_list"),
    path("labor/new/", labor.labor_create, name="labor_create"),
    path("labor/setup/<int:employee_pk>/", labor.labor_setup, name="labor_setup"),
    path("labor/<int:pk>/", labor.labor_detail, name="labor_detail"),
    path("labor/<int:pk>/edit/", labor.labor_edit, name="labor_edit"),
    path("labor/<int:pk>/pay/", labor.labor_rate_change, name="labor_rate_change"),
    path("labor/<int:pk>/allocate/", labor_sites.labor_allocate, name="labor_allocate"),
    path("labor/<int:pk>/release/", labor_sites.labor_release, name="labor_release"),
    path("labor/transfer/", labor_sites.labor_transfer, name="labor_transfer"),
    path("labor/sites/", labor_sites.site_list, name="labor_site_list"),
    path("labor/sites/<int:project_pk>/<int:location_pk>/", labor_sites.site_detail, name="labor_site_detail"),
    path("labor/attendance/", labor_attendance.labor_attendance, name="labor_attendance"),
    path("labor/timesheets/", labor_timesheet.labor_timesheet, name="labor_timesheet"),
    path("labor/overtime/", labor_overtime.labor_overtime, name="labor_overtime"),
    path("labor/overtime/rules/", labor_overtime.labor_overtime_rules, name="labor_overtime_rules"),
    path("labor/overtime/rules/new/", labor_overtime.labor_overtime_rules_new, name="labor_overtime_rules_new"),
    path("labor/work-orders/", labor_sites.work_order_list, name="labor_work_order_list"),
    path("labor/work-orders/new/", labor_sites.work_order_create, name="labor_work_order_create"),
    path("labor/work-orders/<int:pk>/edit/", labor_sites.work_order_edit, name="labor_work_order_edit"),
    path("labor/work-orders/<int:pk>/toggle/", labor_sites.work_order_toggle, name="labor_work_order_toggle"),
    path("labor/contractors/", labor.contractor_list, name="labor_contractor_list"),
    path("labor/contractors/new/", labor.contractor_create, name="labor_contractor_create"),
    path("labor/contractors/<int:pk>/edit/", labor.contractor_edit, name="labor_contractor_edit"),
    path("labor/contractors/<int:pk>/toggle/", labor.contractor_toggle, name="labor_contractor_toggle"),
    path("labor/trades/", labor.trade_list, name="labor_trade_list"),
    path("labor/trades/new/", labor.trade_create, name="labor_trade_create"),
    path("labor/trades/<int:pk>/edit/", labor.trade_edit, name="labor_trade_edit"),
    path("labor/trades/<int:pk>/toggle/", labor.trade_toggle, name="labor_trade_toggle"),
]
