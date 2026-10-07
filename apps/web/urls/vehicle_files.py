from django.urls import path

from apps.web.views import vehicle_files as v


urlpatterns = [
    path("vehicles/<int:pk>/files/", v.vehicle_files, name="vehicle_files"),
    path("vehicles/files/<slug:target_kind>/<int:pk>/add/", v.vehicle_file_add, name="vehicle_file_add"),
    path("vehicles/files/<int:pk>/", v.vehicle_file, name="vehicle_file"),
    path("vehicles/files/<int:pk>/remove/", v.vehicle_file_remove, name="vehicle_file_remove"),
]
