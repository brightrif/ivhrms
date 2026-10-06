from django.contrib.auth import views as auth_views
from django.urls import path

from . import compliance_views,hr_views, views,company_views, org_views

app_name = "web"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("login/", auth_views.LoginView.as_view(template_name="web/login.html",
                                                redirect_authenticated_user=True), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("password/change/", views.PasswordChangeView.as_view(), name="password_change"),

    path("leave/", views.leave_list, name="leave_list"),
    path("leave/apply/", views.leave_apply, name="leave_apply"),
    path("leave/preview/", views.leave_preview, name="leave_preview"),
    path("leave/<int:pk>/cancel/", views.leave_cancel, name="leave_cancel"),

    path("approvals/", views.approvals_inbox, name="approvals"),
    path("approvals/count/", views.approval_count, name="approval_count"),
    path("approvals/<int:pk>/row/", views.approval_row, name="approval_row"),
    path("approvals/<int:pk>/reject-form/", views.approval_reject_form, name="approval_reject_form"),
    path("approvals/<int:pk>/decide/", views.approval_decide, name="approval_decide"),

    path("attendance/", views.attendance, name="attendance"),
    path("attendance/<str:day>/row/", views.attendance_row, name="attendance_row"),
    path("attendance/<str:day>/correct/", views.attendance_correct, name="attendance_correct"),

    path("staff/", hr_views.employee_list, name="employee_list"),
    path("staff/new/", hr_views.employee_create, name="employee_create"),
    path("staff/company-fields/", hr_views.employee_company_fields, name="employee_company_fields"),
    path("staff/<int:pk>/", hr_views.employee_detail, name="employee_detail"),
    path("staff/<int:pk>/edit/", hr_views.employee_edit, name="employee_edit"),
    path("staff/<int:pk>/assignment/", hr_views.employee_assign, name="employee_assign"),
    path("staff/<int:pk>/login/", hr_views.employee_login, name="employee_login"),
    path("staff/next-number/", hr_views.employee_next_number, name="employee_next_number"),


    path("companies/", company_views.company_list, name="company_list"),
    path("companies/new/", company_views.company_create, name="company_create"),
    path("companies/<int:pk>/edit/", company_views.company_edit, name="company_edit"),
    path("companies/<int:pk>/toggle/", company_views.company_toggle, name="company_toggle"),
    path("companies/<int:pk>/delete/", company_views.company_delete, name="company_delete"),

    path("departments/", org_views.department_list, name="department_list"),
    path("departments/new/", org_views.department_create, name="department_create"),
    path("departments/<int:pk>/edit/", org_views.department_edit, name="department_edit"),
    path("departments/<int:pk>/toggle/", org_views.department_toggle, name="department_toggle"),
    path("departments/<int:pk>/delete/", org_views.department_delete, name="department_delete"),

    path("designations/", org_views.designation_list, name="designation_list"),
    path("designations/new/", org_views.designation_create, name="designation_create"),
    path("designations/<int:pk>/edit/", org_views.designation_edit, name="designation_edit"),
    path("designations/<int:pk>/toggle/", org_views.designation_toggle, name="designation_toggle"),
    path("designations/<int:pk>/delete/", org_views.designation_delete, name="designation_delete"),

    path("compliance/", compliance_views.dashboard, name="compliance_dashboard"),
    path("compliance/count/", compliance_views.attention_badge, name="compliance_count"),
    path("compliance/documents/", compliance_views.document_list, name="compliance_documents"),
    path("compliance/documents/new/", compliance_views.document_create, name="compliance_create"),
    path("compliance/documents/<int:pk>/", compliance_views.document_detail, name="compliance_detail"),
    path("compliance/documents/<int:pk>/edit/", compliance_views.document_edit, name="compliance_edit"),
    path("compliance/documents/<int:pk>/renew/", compliance_views.document_renew, name="compliance_renew"),
    path("compliance/documents/<int:pk>/file/", compliance_views.document_file, name="compliance_file"),
    path("compliance/documents/<int:pk>/renewal/new/", compliance_views.task_create, name="compliance_task_create"),
    path("compliance/renewals/<int:pk>/cancel/", compliance_views.task_cancel, name="compliance_task_cancel"),
    path("compliance/renewals/<int:pk>/payment/new/", compliance_views.payment_create, name="compliance_payment_create"),
    path("compliance/payments/<int:pk>/receipt/", compliance_views.payment_receipt, name="compliance_receipt"),
    path("compliance/costs/", compliance_views.costs, name="compliance_costs"),
    path("compliance/types/", compliance_views.type_list, name="compliance_types"),
    path("compliance/types/new/", compliance_views.type_create, name="compliance_type_create"),
    path("compliance/types/<int:pk>/edit/", compliance_views.type_edit, name="compliance_type_edit"),
    path("compliance/types/<int:pk>/toggle/", compliance_views.type_toggle, name="compliance_type_toggle"),
]