from django.urls import path

from apps.web.views import dashboard


urlpatterns = [
    path("", dashboard.dashboard, name="dashboard"),
]
