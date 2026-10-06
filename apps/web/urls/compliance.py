from django.urls import path

from apps.web.views import compliance


urlpatterns = [
    path("compliance/", compliance.dashboard, name="compliance_dashboard"),
    path("compliance/count/", compliance.attention_badge, name="compliance_count"),
    path("compliance/documents/", compliance.document_list, name="compliance_documents"),
    path("compliance/documents/new/", compliance.document_create, name="compliance_create"),
    path("compliance/documents/<int:pk>/", compliance.document_detail, name="compliance_detail"),
    path("compliance/documents/<int:pk>/edit/", compliance.document_edit, name="compliance_edit"),
    path("compliance/documents/<int:pk>/renew/", compliance.document_renew, name="compliance_renew"),
    path("compliance/documents/<int:pk>/file/", compliance.document_file, name="compliance_file"),
    path("compliance/documents/<int:pk>/renewal/new/", compliance.task_create, name="compliance_task_create"),
    path("compliance/renewals/<int:pk>/cancel/", compliance.task_cancel, name="compliance_task_cancel"),
    path("compliance/renewals/<int:pk>/payment/new/", compliance.payment_create, name="compliance_payment_create"),
    path("compliance/payments/<int:pk>/receipt/", compliance.payment_receipt, name="compliance_receipt"),
    path("compliance/costs/", compliance.costs, name="compliance_costs"),
    path("compliance/types/", compliance.type_list, name="compliance_types"),
    path("compliance/types/new/", compliance.type_create, name="compliance_type_create"),
    path("compliance/types/<int:pk>/edit/", compliance.type_edit, name="compliance_type_edit"),
    path("compliance/types/<int:pk>/toggle/", compliance.type_toggle, name="compliance_type_toggle"),
]
