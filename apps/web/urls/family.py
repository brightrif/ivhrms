from django.urls import path

from apps.web.views import family, family_eligibility, family_status

# Names start with "employee_" so the Staff entry in the sidebar stays highlighted on these pages.
urlpatterns = [
    path("staff/family/", family.family_list, name="employee_family"),
    path("staff/family/new/", family_eligibility.family_add, name="employee_family_add"),
    path("staff/family/pick/", family_eligibility.family_pick, name="employee_family_pick"),
    path("staff/<int:pk>/family-visa/", family_eligibility.family_visa_edit,
         name="employee_family_visa_edit"),
    path("staff/<int:pk>/family-status/", family_status.status_add, name="employee_family_status_add"),
    path("staff/family/status/", family_status.status_list, name="employee_family_status"),
    path("staff/family/status/<int:pk>/", family_status.status_detail,
         name="employee_family_status_request"),
    path("staff/family/status/<int:pk>/forward/", family_status.status_forward,
         name="employee_family_status_forward"),
    path("staff/family/status/<int:pk>/decline/", family_status.status_decline,
         name="employee_family_status_decline"),
    path("staff/family/status/<int:pk>/withdraw/", family_status.status_withdraw,
         name="employee_family_status_withdraw"),
    path("my/family/", family_status.my_family, name="my_family"),
    path("my/family/request/", family_status.my_family_request, name="my_family_request"),
    path("my/family/<int:pk>/withdraw/", family_status.my_family_withdraw, name="my_family_withdraw"),
    path("my/family/file/<str:kind>/<int:pk>/", family_status.my_family_file, name="my_family_file"),
    path("staff/family/<int:pk>/", family.family_detail, name="employee_family_detail"),
    path("staff/family/<int:pk>/edit/", family.family_edit, name="employee_family_edit"),
    path("staff/family/<int:pk>/deactivate/", family.family_deactivate, name="employee_family_deactivate"),
    path("staff/family/<int:pk>/document/new/", family.family_document_add, name="employee_family_document_add"),
    path("staff/family/<int:pk>/paper/new/", family.family_paper_add, name="employee_family_paper_add"),
    path("staff/family/papers/<int:pk>/", family.family_paper, name="employee_family_paper"),
]
