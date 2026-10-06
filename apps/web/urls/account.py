from django.contrib.auth import views as auth_views
from django.urls import path

from apps.web.views import account


urlpatterns = [
    path("login/", auth_views.LoginView.as_view(template_name="web/login.html",
                                                redirect_authenticated_user=True), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("password/change/", account.PasswordChangeView.as_view(), name="password_change"),
]
