from django.urls import path

from apps.web.views import settings_hub, settings_users

urlpatterns = [
    path("settings/", settings_hub.settings_home, name="settings_home"),
    path("settings/access/", settings_hub.settings_access, name="settings_access"),
    path("settings/users/", settings_users.users_list, name="settings_users"),
    path("settings/users/new/", settings_users.users_add, name="settings_user_add"),
    path("settings/users/for-employee/", settings_users.users_for_employee, name="settings_user_for_employee"),
    path("settings/users/for-employee/<int:pk>/create/", settings_users.users_create_login,
         name="settings_user_create_login"),
    path("settings/users/<int:pk>/", settings_users.users_detail, name="settings_user"),
    path("settings/users/<int:pk>/reset/", settings_users.users_reset, name="settings_user_reset"),
    path("settings/<slug:module>/", settings_hub.settings_module, name="settings_module"),
]
