from django.urls import path

from apps.web.views import vehicle_custody as v


urlpatterns = [
    path("vehicles/<int:pk>/workshop/send/", v.vehicle_workshop_send, name="vehicle_workshop_send"),
    path("vehicles/<int:pk>/workshop/back/", v.vehicle_workshop_back, name="vehicle_workshop_back"),
    path("vehicles/<int:pk>/handover/", v.vehicle_handover, name="vehicle_handover"),
    path("vehicles/<int:pk>/custody/", v.vehicle_custody_request, name="vehicle_custody_request"),
    path("vehicles/custody/<int:pk>/cancel/", v.vehicle_custody_cancel, name="vehicle_custody_cancel"),
    path("vehicles/fines/recover/", v.vehicle_fines_recover, name="vehicle_fines_recover"),
    path("vehicles/fines/<int:pk>/recover/", v.vehicle_fine_recover, name="vehicle_fine_recover"),
    path("vehicles/fines/<int:pk>/unrecover/", v.vehicle_fine_unrecover, name="vehicle_fine_unrecover"),
]
