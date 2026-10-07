from django.urls import path

from apps.web.views import vehicle_incidents as v


urlpatterns = [
    path("vehicles/fines/", v.vehicle_fines, name="vehicle_fines"),
    path("vehicles/<int:pk>/incidents/", v.vehicle_incidents, name="vehicle_incidents"),
    path("vehicles/<int:pk>/fines/new/", v.vehicle_fine_add, name="vehicle_fine_add"),
    path("vehicles/fines/<int:pk>/edit/", v.vehicle_fine_edit, name="vehicle_fine_edit"),
    path("vehicles/fines/<int:pk>/pay/", v.vehicle_fine_pay, name="vehicle_fine_pay"),
    path("vehicles/fines/<int:pk>/cancel/", v.vehicle_fine_void, name="vehicle_fine_void"),
    path("vehicles/<int:pk>/accidents/new/", v.vehicle_accident_add, name="vehicle_accident_add"),
    path("vehicles/accidents/<int:pk>/", v.vehicle_accident, name="vehicle_accident"),
    path("vehicles/accidents/<int:pk>/edit/", v.vehicle_accident_edit, name="vehicle_accident_edit"),
    path("vehicles/accidents/<int:pk>/close/", v.vehicle_accident_close, name="vehicle_accident_close"),
    path("vehicles/accidents/<int:pk>/reopen/", v.vehicle_accident_reopen, name="vehicle_accident_reopen"),
    path("vehicles/accidents/<int:pk>/cancel/", v.vehicle_accident_void, name="vehicle_accident_void"),
    path("vehicles/accidents/<int:pk>/repairs/link/", v.vehicle_accident_link, name="vehicle_accident_link"),
    path("vehicles/accidents/<int:pk>/repairs/<int:record_pk>/unlink/", v.vehicle_accident_unlink,
         name="vehicle_accident_unlink"),
]
