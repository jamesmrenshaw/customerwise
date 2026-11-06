"""Invitations and magic-link sign-in (D02).

Rules:
- Only users created by Customer Wise staff can sign in. An unknown email never gets a link,
  and the response is the same either way, so emails can't be probed.
- Secrets are random, single use and stored only as SHA-256 hashes.
- Invitations last 7 days; resending one replaces the old link.
- Sign-in links last 15 minutes. A user can request at most MAX_LINKS_PER_WINDOW in
  LINK_WINDOW, which limits email spam.
"""

import contextlib
import hashlib
import logging
import secrets
from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from .models import Invitation, LoginToken, User

logger = logging.getLogger(__name__)

INVITATION_LIFETIME = timedelta(days=7)
LOGIN_LINK_LIFETIME = timedelta(minutes=15)
LINK_WINDOW = timedelta(minutes=15)
MAX_LINKS_PER_WINDOW = 5


class EmailNotSent(Exception):
    """The email provider rejected the message or is not configured."""


def _new_secret():
    secret = secrets.token_urlsafe(32)
    return secret, hash_secret(secret)


def hash_secret(secret):
    return hashlib.sha256(secret.encode()).hexdigest()


def absolute_url(path):
    return settings.SITE_URL.rstrip("/") + path


def _send(user, subject, template, context):
    body = render_to_string(template, {"user": user, **context})
    try:
        send_mail(subject, body, None, [user.email])
    except Exception as exc:
        logger.exception("Email to user %s failed", user.pk)
        raise EmailNotSent(str(exc)) from exc


# --- Invitations ------------------------------------------------------------------


@transaction.atomic
def create_invitation(user, sent_by=None):
    """Create a fresh 7-day invitation, replacing any pending one. Returns (invitation, secret)."""
    now = timezone.now()
    Invitation.objects.filter(user=user, used_at__isnull=True, revoked_at__isnull=True).update(revoked_at=now)
    secret, token_hash = _new_secret()
    invitation = Invitation.objects.create(
        user=user,
        sent_by=sent_by,
        token_hash=token_hash,
        created_at=now,
        expires_at=now + INVITATION_LIFETIME,
    )
    return invitation, secret


def send_invitation(user, sent_by=None):
    if not user.is_active:
        raise ValueError("Deactivated users cannot be invited.")
    invitation, secret = create_invitation(user, sent_by)
    link = absolute_url(reverse("accounts:accept_invitation", args=[secret]))
    _send(
        user,
        "You're invited to Customer Wise",
        "accounts/email/invitation.txt",
        {"link": link, "expires_at": invitation.expires_at},
    )
    from apps.audit.services import record

    record(sent_by, "user.invited", user, email=user.email, role=user.get_role_display())
    return invitation


def find_invitation(secret):
    """Return the usable invitation for this secret, or None."""
    invitation = Invitation.objects.select_related("user").filter(token_hash=hash_secret(secret)).first()
    if invitation and invitation.is_usable and invitation.user.is_active:
        return invitation
    return None


@transaction.atomic
def accept_invitation(secret):
    """Use the invitation and return its user, or None if it is not usable."""
    invitation = find_invitation(secret)
    if invitation is None:
        return None
    # Lock and re-check so a double submit can't use the link twice.
    invitation = Invitation.objects.select_for_update().get(pk=invitation.pk)
    if not invitation.is_usable:
        return None
    invitation.used_at = timezone.now()
    invitation.save(update_fields=["used_at"])
    return invitation.user


# --- Magic sign-in links ----------------------------------------------------------


def create_login_token(user):
    secret, token_hash = _new_secret()
    now = timezone.now()
    LoginToken.objects.create(
        user=user, token_hash=token_hash, created_at=now, expires_at=now + LOGIN_LINK_LIFETIME
    )
    return secret


def request_login_link(email):
    """Email a sign-in link if this address belongs to an active user.

    Always returns None and never reveals whether the email is known. Over the rate limit,
    the request is ignored quietly.
    """
    user = User.objects.filter(email__iexact=email.strip(), is_active=True).first()
    if user is None:
        return
    recent = LoginToken.objects.filter(user=user, created_at__gt=timezone.now() - LINK_WINDOW).count()
    if recent >= MAX_LINKS_PER_WINDOW:
        logger.warning("Sign-in link rate limit reached for user %s", user.pk)
        return
    secret = create_login_token(user)
    link = absolute_url(reverse("accounts:magic_link", args=[secret]))
    # A failure is already logged. The person sees the same "check your email" page either way.
    with contextlib.suppress(EmailNotSent):
        _send(user, "Your Customer Wise sign-in link", "accounts/email/magic_link.txt", {"link": link})


def find_login_token(secret):
    token = LoginToken.objects.select_related("user").filter(token_hash=hash_secret(secret)).first()
    if token and token.is_usable and token.user.is_active:
        return token
    return None


@transaction.atomic
def use_login_token(secret):
    """Use the sign-in link and return its user, or None if it is not usable."""
    token = find_login_token(secret)
    if token is None:
        return None
    token = LoginToken.objects.select_for_update().get(pk=token.pk)
    if not token.is_usable:
        return None
    token.used_at = timezone.now()
    token.save(update_fields=["used_at"])
    # Signing in by email proves the address, so it also completes a pending invitation.
    Invitation.objects.filter(user=token.user, used_at__isnull=True, revoked_at__isnull=True).update(
        used_at=token.used_at
    )
    return token.user
