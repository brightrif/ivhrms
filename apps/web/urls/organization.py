from django.urls import path

from apps.web.views import organization


urlpatterns = [
    path("organization/", organization.organization_home, name="organization_home"),
    path("organization/projects-setting/<int:pk>/", organization.company_projects_set, name="company_projects_set"),
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
    path("projects/", organization.project_list, name="project_list"),
    path("projects/new/", organization.project_create, name="project_create"),
    path("projects/<int:pk>/edit/", organization.project_edit, name="project_edit"),
    path("projects/<int:pk>/toggle/", organization.project_toggle, name="project_toggle"),
    path("projects/<int:pk>/delete/", organization.project_delete, name="project_delete"),
    path("sites/", organization.site_list, name="site_list"),
    path("sites/new/", organization.site_create, name="site_create"),
    path("sites/<int:pk>/edit/", organization.site_edit, name="site_edit"),
    path("sites/<int:pk>/toggle/", organization.site_toggle, name="site_toggle"),
    path("sites/<int:pk>/delete/", organization.site_delete, name="site_delete"),
    path("sites/code-preview/", organization.site_code_preview, name="site_code_preview"),
    path("grades/new/", organization.grade_create, name="grade_create"),
    path("grades/<int:pk>/edit/", organization.grade_edit, name="grade_edit"),
    path("grades/<int:pk>/delete/", organization.grade_delete, name="grade_delete"),
    path("sites/search/", organization.site_search, name="site_search"),
    path("projects/code-preview/", organization.project_code_preview, name="project_code_preview"),
]
