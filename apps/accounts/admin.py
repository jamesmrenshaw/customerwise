from django.contrib import admin

from .models import User


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    """Basic user admin. Invitations and resend actions arrive with sign-in (CW-004)."""

    list_display = ["email", "full_name", "role", "is_active", "last_login"]
    list_filter = ["role", "is_active"]
    search_fields = ["email", "full_name"]
    fields = ["email", "full_name", "role", "is_active", "last_login", "date_joined"]
    readonly_fields = ["last_login", "date_joined"]
