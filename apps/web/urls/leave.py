from django.urls import path

from apps.web.views import leave


urlpatterns = [
    path("leave/", leave.leave_list, name="leave_list"),
    path("leave/apply/", leave.leave_apply, name="leave_apply"),
    path("leave/preview/", leave.leave_preview, name="leave_preview"),
    path("leave/<int:pk>/cancel/", leave.leave_cancel, name="leave_cancel"),
]
