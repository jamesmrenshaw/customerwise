import uuid

from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create_user(self, email, **extra_fields):
        if not email:
            raise ValueError("An email address is required.")
        user = self.model(email=self.normalize_email(email), **extra_fields)
        # No passwords: sign-in is by magic link or Microsoft/Google (D02).
        user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_user(self, email, **extra_fields):
        extra_fields.setdefault("role", User.Role.SHOPPER)
        return self._create_user(email, **extra_fields)

    def create_staff(self, email, **extra_fields):
        extra_fields.setdefault("role", User.Role.STAFF)
        return self._create_user(email, **extra_fields)

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.update(role=User.Role.STAFF, is_superuser=True)
        return self._create_user(email, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    """A Customer Wise staff member or a shopper (D03). Clients have no logins in the pilot."""

    class Role(models.TextChoices):
        STAFF = "staff", "Customer Wise staff"
        SHOPPER = "shopper", "Shopper"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField("email address", unique=True)
    full_name = models.CharField(max_length=200, blank=True)
    role = models.CharField(max_length=20, choices=Role, default=Role.SHOPPER)
    # Shopper directory fields (CW-006, pilot minimum). Staff only.
    reference = models.CharField(max_length=50, blank=True, help_text="Internal shopper reference.")
    internal_notes = models.TextField(blank=True, help_text="Customer Wise staff only.")
    is_active = models.BooleanField(
        default=True,
        help_text="Untick to deactivate. The user is signed out on their next request.",
    )
    date_joined = models.DateTimeField(default=timezone.now)

    objects = UserManager()

    USERNAME_FIELD = "email"
    EMAIL_FIELD = "email"
    REQUIRED_FIELDS = []

    class Meta:
        constraints = [
            models.UniqueConstraint(Lower("email"), name="accounts_user_email_ci_unique"),
            models.CheckConstraint(
                condition=models.Q(role="staff") | models.Q(is_superuser=False),
                name="accounts_user_only_staff_superuser",
            ),
        ]
        ordering = ["email"]

    def __str__(self):
        return self.full_name or self.email

    @property
    def is_staff(self):
        """Admin access follows the role. Shoppers can never reach Django admin (D03)."""
        return self.role == self.Role.STAFF and self.is_active

    @property
    def is_shopper(self):
        return self.role == self.Role.SHOPPER

    # All staff are equal in the pilot (D03): every active staff user has every
    # admin permission, and shoppers have none.
    def has_perm(self, perm, obj=None):
        return self.is_staff or super().has_perm(perm, obj)

    def has_module_perms(self, app_label):
        return self.is_staff or super().has_module_perms(app_label)

    def save(self, *args, **kwargs):
        self.email = self.email.strip().lower()
        if self.role != self.Role.STAFF:
            self.is_superuser = False
        super().save(*args, **kwargs)


class TokenQuerySet(models.QuerySet):
    def usable(self):
        return self.filter(expires_at__gt=timezone.now(), used_at__isnull=True)


class BaseToken(models.Model):
    """A single-use secret sent by email. Only a SHA-256 hash of the secret is stored."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    token_hash = models.CharField(max_length=64, unique=True, editable=False)
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)

    objects = TokenQuerySet.as_manager()

    class Meta:
        abstract = True
        ordering = ["-created_at"]

    @property
    def is_usable(self):
        return self.used_at is None and self.expires_at > timezone.now()


class Invitation(BaseToken):
    """An invitation to a user created by staff in admin. Valid for 7 days (D02)."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="invitations")
    sent_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    revoked_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Invitation for {self.user.email}"

    @property
    def is_usable(self):
        return super().is_usable and self.revoked_at is None

    @property
    def status(self):
        if self.used_at:
            return "Accepted"
        if self.revoked_at:
            return "Replaced"
        if self.expires_at <= timezone.now():
            return "Expired"
        return "Pending"


class LoginToken(BaseToken):
    """A magic sign-in link. Single use, valid for 15 minutes (D02)."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="login_tokens")

    def __str__(self):
        return f"Sign-in link for {self.user.email}"
