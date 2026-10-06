from django.urls import path

from apps.web.views import attendance


urlpatterns = [
    path("attendance/", attendance.attendance, name="attendance"),
    path("attendance/<str:day>/row/", attendance.attendance_row, name="attendance_row"),
    path("attendance/<str:day>/correct/", attendance.attendance_correct, name="attendance_correct"),
]
