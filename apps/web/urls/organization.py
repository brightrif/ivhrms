from django.urls import path

from apps.web.views import organization


urlpatterns = [
    path("departments/", organization.department_list, name="department_list"),
    path("departments/new/", organization.department_create, name="department_create"),
    path("departments/<int:pk>/edit/", organization.department_edit, name="department_edit"),
    path("departments/<int:pk>/toggle/", organization.department_toggle, name="department_toggle"),
    path("departments/<int:pk>/delete/", organization.department_delete, name="department_delete"),
    path("designations/", organization.designation_list, name="designation_list"),
    path("designations/new/", organization.designation_create, name="designation_create"),
    path("designations/<int:pk>/edit/", organization.designation_edit, name="designation_edit"),
    path("designations/<int:pk>/toggle/", organization.designation_toggle, name="designation_toggle"),
    path("designations/<int:pk>/delete/", organization.designation_delete, name="designation_delete"),
]
