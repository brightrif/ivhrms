from django.urls import path

from apps.web.views import settings_hub, settings_users, scheduling_settings, family_status_approver,company_view

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
    path("settings/holidays/", scheduling_settings.holiday_list, name="holiday_list"),
    path("settings/holidays/new/", scheduling_settings.holiday_add, name="holiday_add"),
    path("settings/holidays/<int:pk>/", scheduling_settings.holiday_edit, name="holiday_edit"),
    path("settings/holidays/<int:pk>/toggle/", scheduling_settings.holiday_toggle, name="holiday_toggle"),
    path("settings/weekly-off/", scheduling_settings.weekly_off, name="weekly_off"),
    path("settings/family-status-approver/", family_status_approver.status_approver, name="family_status_approver"),
    path("settings/company-view/", company_view.company_view_set, name="company_view_set"),
    path("settings/users/check/", settings_users.username_check, name="username_check"),
    path("settings/company-colours/<int:pk>/", company_view.company_colours_set, name="company_colours_set"),
    path("company/switch/", company_view.company_switch, name="company_switch"),
    path("settings/<slug:module>/", settings_hub.settings_module, name="settings_module"),
]
