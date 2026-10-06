from django.urls import path

from apps.web.views import vehicle_upkeep as v


urlpatterns = [
    path("vehicles/service-due/", v.vehicle_service_due, name="vehicle_service_due"),
    path("vehicles/<int:pk>/fuel/", v.vehicle_fuel, name="vehicle_fuel"),
    path("vehicles/<int:pk>/fuel/new/", v.vehicle_fuel_add, name="vehicle_fuel_add"),
    path("vehicles/fuel/<int:pk>/cancel/", v.vehicle_fuel_void, name="vehicle_fuel_void"),
    path("vehicles/<int:pk>/service/", v.vehicle_service, name="vehicle_service"),
    path("vehicles/<int:pk>/service/new/", v.vehicle_service_add, name="vehicle_service_add"),
    path("vehicles/<int:pk>/service/plans/new/", v.vehicle_plan_add, name="vehicle_plan_add"),
    path("vehicles/service/plans/<int:pk>/edit/", v.vehicle_plan_edit, name="vehicle_plan_edit"),
    path("vehicles/service/<int:pk>/cancel/", v.vehicle_service_void, name="vehicle_service_void"),
]
