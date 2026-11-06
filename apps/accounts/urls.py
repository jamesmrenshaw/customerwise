from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("login/", views.login_view, name="login"),
    path("login/<str:secret>/", views.magic_link, name="magic_link"),
    path("invite/<str:secret>/", views.accept_invitation, name="accept_invitation"),
    path("logout/", views.logout_view, name="logout"),
]
