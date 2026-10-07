from django.urls import path

from apps.web.views import vehicle_reports as v


urlpatterns = [
    path("vehicles/dashboard/", v.vehicle_dashboard, name="vehicle_dashboard"),
    path("vehicles/costs/", v.vehicle_costs, name="vehicle_costs"),
]
