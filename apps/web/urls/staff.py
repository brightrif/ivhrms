from django.urls import path

from apps.web.views import staff, worker_type


urlpatterns = [
    path("staff/", staff.employee_list, name="employee_list"),
    path("staff/new/", staff.employee_create, name="employee_create"),
    path("staff/company-fields/", staff.employee_company_fields, name="employee_company_fields"),
    path("staff/<int:pk>/", staff.employee_detail, name="employee_detail"),
    path("staff/<int:pk>/edit/", staff.employee_edit, name="employee_edit"),
    path("staff/<int:pk>/assignment/", staff.employee_assign, name="employee_assign"),
    path("staff/<int:pk>/login/", staff.employee_login, name="employee_login"),
    path("staff/next-number/", staff.employee_next_number, name="employee_next_number"),
    path("staff/<int:pk>/to-staff/", worker_type.employee_to_staff, name="employee_to_staff"),
    path("staff/<int:pk>/to-labor/", worker_type.employee_to_labor, name="employee_to_labor"),
]
