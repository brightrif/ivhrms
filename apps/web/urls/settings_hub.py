from django.urls import path

from apps.web.views import settings_hub

urlpatterns = [
    path("settings/", settings_hub.settings_home, name="settings_home"),
    path("settings/access/", settings_hub.settings_access, name="settings_access"),
    path("settings/<slug:module>/", settings_hub.settings_module, name="settings_module"),
]
