"""Password change."""

from django.contrib.auth import views as auth_views
from django.urls import reverse_lazy


class PasswordChangeView(auth_views.PasswordChangeView):
    template_name = "web/password_change.html"
    success_url = reverse_lazy("web:dashboard")

    def form_valid(self, form):
        response = super().form_valid(form)
        user = self.request.user
        if user.must_change_password:
            user.must_change_password = False
            user.save(update_fields=["must_change_password"])
        return response
