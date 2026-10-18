from django.urls import path

from apps.web.views import attendance, staff_attendance


urlpatterns = [
    path("attendance/", attendance.attendance, name="attendance"),
    path("attendance/<str:day>/row/", attendance.attendance_row, name="attendance_row"),
    path("attendance/<str:day>/correct/", attendance.attendance_correct, name="attendance_correct"),
    path("attendance/<str:day>/day/", attendance.attendance_day, name="attendance_day"),
    path("attendance/<str:day>/day/correct/", attendance.attendance_day_correct, name="attendance_day_correct"),
    path("staff/attendance/", staff_attendance.staff_attendance, name="staff_attendance"),
]
