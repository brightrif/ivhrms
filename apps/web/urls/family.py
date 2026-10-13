from django.urls import path

from apps.web.views import family

# Names start with "employee_" so the Staff entry in the sidebar stays highlighted on these pages.
urlpatterns = [
    path("staff/family/", family.family_list, name="employee_family"),
    path("staff/family/new/", family.family_add, name="employee_family_add"),
    path("staff/family/<int:pk>/", family.family_detail, name="employee_family_detail"),
    path("staff/family/<int:pk>/edit/", family.family_edit, name="employee_family_edit"),
    path("staff/family/<int:pk>/deactivate/", family.family_deactivate, name="employee_family_deactivate"),
    path("staff/family/<int:pk>/document/new/", family.family_document_add, name="employee_family_document_add"),
    path("staff/family/<int:pk>/paper/new/", family.family_paper_add, name="employee_family_paper_add"),
    path("staff/family/papers/<int:pk>/", family.family_paper, name="employee_family_paper"),
]