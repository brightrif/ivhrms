"""URLs for the web interface, one file per feature. Names stay under the "web:" namespace."""

from . import (
    account, approvals, attendance, companies, compliance, dashboard, 
    leave, organization, staff,vehicles,vehicle_usage,vehicle_upkeep,
    vehicle_incidents,vehicle_loans,vehicle_reports,vehicle_custody,
    vehicle_files,labor,settings_hub,family_visa,family,airtickets)
# from ..views import family

from . import payroll  # noqa: E402

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
    *vehicle_incidents.urlpatterns,
    *vehicle_loans.urlpatterns,
    *vehicle_reports.urlpatterns,
    *vehicle_custody.urlpatterns,
    *vehicle_files.urlpatterns,
    *labor.urlpatterns,
    *payroll.urlpatterns,
    *settings_hub.urlpatterns,
    *family.urlpatterns,
    *family_visa.urlpatterns,
    *airtickets.urlpatterns,
]
