from django.contrib import admin, messages
from django.shortcuts import get_object_or_404, redirect
from django.urls import path, reverse
from django.views.decorators.http import require_POST

from . import services
from .models import Invitation, User


class InvitationInline(admin.TabularInline):
    model = Invitation
    fk_name = "user"
    extra = 0
    can_delete = False
    fields = ["status", "created_at", "expires_at", "used_at", "sent_by"]
    readonly_fields = fields
    ordering = ["-created_at"]
    verbose_name_plural = "Invitations"

    def has_add_permission(self, request, obj=None):
        return False

    @admin.display(description="Status")
    def status(self, obj):
        return obj.status


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    """Users are created here; the "Send invitation" action emails them a link (CW-004)."""

    list_display = ["email", "full_name", "role", "reference", "is_active", "invitation_status", "last_login"]
    list_filter = ["role", "is_active"]
    search_fields = ["email", "full_name", "reference"]
    fields = [
        "email",
        "full_name",
        "role",
        "is_active",
        "reference",
        "internal_notes",
        "last_login",
        "date_joined",
    ]
    change_form_template = "admin/accounts/user/change_form.html"
    readonly_fields = ["last_login", "date_joined"]
    inlines = [InvitationInline]
    actions = ["send_invitations"]

    @admin.display(description="Invitation")
    def invitation_status(self, obj):
        latest = obj.invitations.first()
        if obj.last_login:
            return "Signed in"
        return latest.status if latest else "Not sent"

    def _invite(self, request, users):
        sent, failed = [], []
        for user in users:
            if not user.is_active:
                failed.append(f"{user.email} (deactivated)")
                continue
            try:
                services.send_invitation(user, sent_by=request.user)
                sent.append(user.email)
            except services.EmailNotSent:
                failed.append(f"{user.email} (email could not be sent)")
        if sent:
            self.message_user(request, f"Invitation sent to {', '.join(sent)}.", messages.SUCCESS)
        if failed:
            self.message_user(
                request,
                "Not sent: "
                + ", ".join(failed)
                + ". Check the email settings (BREVO_API_KEY, DEFAULT_FROM_EMAIL).",
                messages.ERROR,
            )

    @admin.action(description="Send (or resend) invitation email")
    def send_invitations(self, request, queryset):
        self._invite(request, queryset)

    def get_urls(self):
        return [
            path(
                "<uuid:pk>/send-invitation/",
                self.admin_site.admin_view(require_POST(self.send_invitation_view)),
                name="accounts_user_send_invitation",
            ),
            *super().get_urls(),
        ]

    def send_invitation_view(self, request, pk):
        user = get_object_or_404(User, pk=pk)
        self._invite(request, [user])
        return redirect(reverse("admin:accounts_user_change", args=[user.pk]))

    def get_readonly_fields(self, request, obj=None):
        # Only staff can grant the staff role (D03). The field stays editable for staff, and
        # shoppers can never reach admin. Don't let staff lock themselves out.
        readonly = list(super().get_readonly_fields(request, obj))
        if obj is not None and obj == request.user:
            readonly += ["role", "is_active"]
        return readonly
