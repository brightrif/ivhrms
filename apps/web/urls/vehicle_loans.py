from django.urls import path

from apps.web.views import vehicle_loans as v


urlpatterns = [
    path("vehicles/loans/", v.vehicle_loans, name="vehicle_loans"),
    path("vehicles/<int:pk>/loan/", v.vehicle_loan, name="vehicle_loan"),
    path("vehicles/<int:pk>/loan/new/", v.vehicle_loan_add, name="vehicle_loan_add"),
    path("vehicles/loans/installments/<int:pk>/pay/", v.vehicle_installment_pay, name="vehicle_installment_pay"),
    path("vehicles/loans/installments/<int:pk>/undo/", v.vehicle_installment_undo, name="vehicle_installment_undo"),
    path("vehicles/loans/<int:pk>/settle/", v.vehicle_loan_settle, name="vehicle_loan_settle"),
    path("vehicles/loans/<int:pk>/cancel/", v.vehicle_loan_void, name="vehicle_loan_void"),
]
