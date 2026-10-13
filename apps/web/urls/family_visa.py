from django.urls import path

from apps.web.views import family_visa

urlpatterns = [
    path("staff/family/requests/", family_visa.family_requests, name="employee_family_requests"),
    path("staff/family/costs/", family_visa.family_costs, name="employee_family_costs"),
    path("staff/family/receipts/<int:pk>/", family_visa.family_receipt, name="employee_family_receipt"),
    path("staff/family/<int:pk>/request/new/", family_visa.family_request_add, name="employee_family_request_add"),
    path("staff/family/requests/<int:pk>/", family_visa.family_request, name="employee_family_request"),
    path("staff/family/requests/<int:pk>/withdraw/", family_visa.family_request_withdraw,
         name="employee_family_request_withdraw"),
    path("staff/family/requests/<int:pk>/submitted/", family_visa.family_request_submitted,
         name="employee_family_request_submitted"),
    path("staff/family/requests/<int:pk>/complete/", family_visa.family_request_complete,
         name="employee_family_request_complete"),
    path("staff/family/requests/<int:pk>/payment/new/", family_visa.family_request_payment,
         name="employee_family_request_payment"),
]
