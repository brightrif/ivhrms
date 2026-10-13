from django.urls import path

from apps.web.views import payroll

urlpatterns = [
    path("payroll/", payroll.payroll_home, name="payroll_home"),
    path("payroll/employees/<int:pk>/", payroll.payroll_employee, name="payroll_employee"),
    path("payroll/employees/<int:pk>/salary/", payroll.salary_change, name="payroll_salary_change"),
    path("payroll/employees/<int:pk>/salary/correct/", payroll.salary_correct, name="payroll_salary_correct"),
    path("payroll/employees/<int:pk>/payment/", payroll.payment_edit, name="payroll_payment_edit"),
    path("payroll/allowances/", payroll.allowance_list, name="payroll_allowances"),
    path("payroll/allowances/new/", payroll.allowance_create, name="payroll_allowance_create"),
    path("payroll/allowances/<int:pk>/edit/", payroll.allowance_edit, name="payroll_allowance_edit"),
    path("payroll/allowances/<int:pk>/toggle/", payroll.allowance_toggle, name="payroll_allowance_toggle"),
]
