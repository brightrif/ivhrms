from django.urls import path

from apps.web.views import approvals


urlpatterns = [
    path("approvals/", approvals.approvals_inbox, name="approvals"),
    path("approvals/count/", approvals.approval_count, name="approval_count"),
    path("approvals/<int:pk>/row/", approvals.approval_row, name="approval_row"),
    path("approvals/<int:pk>/reject-form/", approvals.approval_reject_form, name="approval_reject_form"),
    path("approvals/<int:pk>/decide/", approvals.approval_decide, name="approval_decide"),
]
