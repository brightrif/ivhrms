"""URLs for the web interface, one file per feature. Names stay under the "web:" namespace."""

from . import (
    account, approvals, attendance, 
    companies, compliance, dashboard, 
    leave, organization, staff,
    vehicles,vehicle_usage,vehicle_upkeep)

app_name = "web"

urlpatterns = [
    *dashboard.urlpatterns,
    *account.urlpatterns,
    *leave.urlpatterns,
    *approvals.urlpatterns,
    *attendance.urlpatterns,
    *staff.urlpatterns,
    *companies.urlpatterns,
    *organization.urlpatterns,
    *compliance.urlpatterns,
    *vehicles.urlpatterns,
    *vehicle_usage.urlpatterns,
    *vehicle_upkeep.urlpatterns,
]
