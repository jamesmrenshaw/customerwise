from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView

from apps.accounts.views import logout_view

admin.site.site_header = "Customer Wise admin"
admin.site.site_title = "Customer Wise admin"
admin.site.index_title = "Users"
admin.site.site_url = "/projects/"

urlpatterns = [
    # There are no passwords, so admin sign-in uses the normal sign-in page (D02).
    path("admin/login/", RedirectView.as_view(pattern_name="accounts:login", query_string=True)),
    path("admin/logout/", logout_view),
    path("admin/", admin.site.urls),
    path("accounts/", include("apps.accounts.urls")),
    path("", include("apps.staff.urls")),
    path("", include("apps.core.urls")),
]
