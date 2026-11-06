from django.contrib import admin
from django.urls import include, path

admin.site.site_header = "Customer Wise admin"
admin.site.site_title = "Customer Wise admin"
admin.site.index_title = "Pilot setup"

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("apps.core.urls")),
]
