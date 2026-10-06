from django.urls import path

from apps.web.views import vehicle_usage


urlpatterns = [
    path("vehicles/<int:pk>/assign/", vehicle_usage.vehicle_assign, name="vehicle_assign"),
    path("vehicles/assignments/<int:pk>/return/", vehicle_usage.vehicle_return, name="vehicle_return"),
    path("vehicles/<int:pk>/odometer/", vehicle_usage.vehicle_odometer, name="vehicle_odometer"),
]
