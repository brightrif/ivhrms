from django.urls import path

from apps.web.views import staff


urlpatterns = [
    path("staff/", staff.employee_list, name="employee_list"),
    path("staff/new/", staff.employee_create, name="employee_create"),
    path("staff/company-fields/", staff.employee_company_fields, name="employee_company_fields"),
    path("staff/<int:pk>/", staff.employee_detail, name="employee_detail"),
    path("staff/<int:pk>/edit/", staff.employee_edit, name="employee_edit"),
    path("staff/<int:pk>/assignment/", staff.employee_assign, name="employee_assign"),
    path("staff/<int:pk>/login/", staff.employee_login, name="employee_login"),
    path("staff/next-number/", staff.employee_next_number, name="employee_next_number"),
]
