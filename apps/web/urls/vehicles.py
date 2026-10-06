from django.urls import path

from apps.web.views import vehicles


urlpatterns = [
    path("vehicles/", vehicles.vehicle_list, name="vehicle_list"),
    path("vehicles/new/", vehicles.vehicle_create, name="vehicle_create"),
    path("vehicles/<int:pk>/", vehicles.vehicle_detail, name="vehicle_detail"),
    path("vehicles/<int:pk>/edit/", vehicles.vehicle_edit, name="vehicle_edit"),
    path("vehicles/<int:pk>/sell/", vehicles.vehicle_sell, name="vehicle_sell"),
    path("vehicles/<int:pk>/documents/new/", vehicles.vehicle_document_create, name="vehicle_document_create"),
    path("vehicles/documents/<int:pk>/edit/", vehicles.vehicle_document_edit, name="vehicle_document_edit"),
]
