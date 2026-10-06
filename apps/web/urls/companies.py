from django.urls import path

from apps.web.views import companies


urlpatterns = [
    path("companies/", companies.company_list, name="company_list"),
    path("companies/new/", companies.company_create, name="company_create"),
    path("companies/<int:pk>/edit/", companies.company_edit, name="company_edit"),
    path("companies/<int:pk>/toggle/", companies.company_toggle, name="company_toggle"),
    path("companies/<int:pk>/delete/", companies.company_delete, name="company_delete"),
]
