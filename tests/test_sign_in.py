import re
from datetime import timedelta

import pytest
from django.core import mail
from django.core.management import call_command
from django.http import HttpResponse
from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone

from apps.accounts import services
from apps.accounts.models import Invitation, LoginToken, User
from apps.core.access import shopper_required, staff_required

pytestmark = pytest.mark.django_db

LINK = re.compile(r"https?://\S+")


@pytest.fixture
def shopper():
    return User.objects.create_user("sam@example.com", full_name="Sam Okafor")


@pytest.fixture
def staff():
    return User.objects.create_staff("alex@customerwise.example", full_name="Alex Morgan")


def path_from_last_email():
    return LINK.search(mail.outbox[-1].body).group().replace("http://localhost:8000", "")


def request_link(client, email):
    return client.post(reverse("accounts:login"), {"email": email})


# --- Magic links -------------------------------------------------------------------


def test_invited_user_receives_a_sign_in_link(client, shopper):
    response = request_link(client, "SAM@example.com")
    assert response.status_code == 200
    assert "Check your email" in response.content.decode()
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == ["sam@example.com"]
    assert "expires in 15 minutes" in mail.outbox[0].body


def test_unknown_email_gets_same_page_and_no_email(client):
    response = request_link(client, "stranger@example.com")
    assert response.status_code == 200
    assert "Check your email" in response.content.decode()
    assert mail.outbox == []


def test_deactivated_user_gets_no_link(client, shopper):
    shopper.is_active = False
    shopper.save()
    request_link(client, shopper.email)
    assert mail.outbox == []


def test_opening_link_does_not_sign_in_until_continue_is_pressed(client, shopper):
    request_link(client, shopper.email)
    path = path_from_last_email()
    response = client.get(path)
    assert response.status_code == 200
    assert "Continue" in response.content.decode()
    assert "_auth_user_id" not in client.session
    assert LoginToken.objects.usable().count() == 1  # a link scanner's GET didn't use it


def test_continue_signs_in_and_link_works_only_once(client, shopper):
    request_link(client, shopper.email)
    path = path_from_last_email()
    response = client.post(path)
    assert response.status_code == 302
    assert client.session["_auth_user_id"] == str(shopper.pk)
    client.logout()
    assert client.post(path).status_code == 410
    assert "_auth_user_id" not in client.session


def test_expired_link_is_refused(client, shopper):
    secret = services.create_login_token(shopper)
    LoginToken.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
    response = client.post(reverse("accounts:magic_link", args=[secret]))
    assert response.status_code == 410
    assert "_auth_user_id" not in client.session


def test_link_for_user_deactivated_after_sending_is_refused(client, shopper):
    secret = services.create_login_token(shopper)
    shopper.is_active = False
    shopper.save()
    assert client.post(reverse("accounts:magic_link", args=[secret])).status_code == 410


def test_made_up_token_is_refused(client):
    assert client.get(reverse("accounts:magic_link", args=["not-a-real-token"])).status_code == 410


def test_tokens_are_stored_only_as_hashes(shopper):
    secret = services.create_login_token(shopper)
    token = LoginToken.objects.get()
    assert secret not in token.token_hash
    assert token.token_hash == services.hash_secret(secret)


def test_sign_in_requests_are_rate_limited(client, shopper):
    for _ in range(services.MAX_LINKS_PER_WINDOW + 3):
        request_link(client, shopper.email)
    assert len(mail.outbox) == services.MAX_LINKS_PER_WINDOW


def test_next_redirect_is_kept_but_only_for_this_site(client, staff):
    secret = services.create_login_token(staff)
    url = reverse("accounts:magic_link", args=[secret])
    response = client.post(url, {"next": "/admin/"})
    assert response["Location"] == "/admin/"
    secret = services.create_login_token(staff)
    url = reverse("accounts:magic_link", args=[secret])
    response = client.post(url, {"next": "https://evil.example/"})
    assert response["Location"] == reverse("core:home")


def test_sign_out_needs_post(client, shopper):
    client.force_login(shopper)
    assert client.get(reverse("accounts:logout")).status_code == 405
    client.post(reverse("accounts:logout"))
    assert "_auth_user_id" not in client.session


# --- Invitations -------------------------------------------------------------------


def test_invitation_email_lasts_seven_days_and_signs_in(client, shopper, staff):
    invitation = services.send_invitation(shopper, sent_by=staff)
    assert invitation.expires_at - invitation.created_at == timedelta(days=7)
    path = path_from_last_email()
    assert "/accounts/invite/" in path
    assert "Welcome to Customer Wise" in client.get(path).content.decode()
    client.post(path)
    assert client.session["_auth_user_id"] == str(shopper.pk)
    invitation.refresh_from_db()
    assert invitation.status == "Accepted"
    assert client.post(path).status_code == 410


def test_resending_replaces_the_old_invitation(client, shopper):
    services.send_invitation(shopper)
    first = path_from_last_email()
    services.send_invitation(shopper)
    second = path_from_last_email()
    assert client.get(first).status_code == 410
    assert client.get(second).status_code == 200
    assert [i.status for i in Invitation.objects.order_by("created_at")] == ["Replaced", "Pending"]


def test_expired_invitation_is_refused(client, shopper):
    _, secret = services.create_invitation(shopper)
    Invitation.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
    assert client.post(reverse("accounts:accept_invitation", args=[secret])).status_code == 410


def test_deactivated_users_cannot_be_invited(shopper):
    shopper.is_active = False
    shopper.save()
    with pytest.raises(ValueError):
        services.send_invitation(shopper)


def test_signing_in_by_magic_link_completes_a_pending_invitation(client, shopper):
    services.create_invitation(shopper)
    secret = services.create_login_token(shopper)
    client.post(reverse("accounts:magic_link", args=[secret]))
    assert Invitation.objects.get().status == "Accepted"


def test_admin_action_sends_invitations(client, staff, shopper):
    client.force_login(staff)
    response = client.post(
        reverse("admin:accounts_user_changelist"),
        {"action": "send_invitations", "_selected_action": [shopper.pk]},
        follow=True,
    )
    assert "Invitation sent to sam@example.com" in response.content.decode()
    assert mail.outbox[-1].to == ["sam@example.com"]


def test_admin_action_reports_email_failures(client, staff, shopper, settings):
    settings.MAILERS = {"default": {"BACKEND": "tests.failing_mail.EmailBackend"}}
    client.force_login(staff)
    response = client.post(
        reverse("admin:accounts_user_changelist"),
        {"action": "send_invitations", "_selected_action": [shopper.pk]},
        follow=True,
    )
    assert "email could not be sent" in response.content.decode()


# --- Admin and access checks -------------------------------------------------------


def test_admin_login_uses_the_sign_in_page(client):
    response = client.get("/admin/login/?next=/admin/")
    assert response.status_code == 302
    assert response["Location"] == reverse("accounts:login") + "?next=/admin/"


def test_staff_cannot_demote_or_deactivate_themselves_in_admin(client, staff):
    client.force_login(staff)
    response = client.get(reverse("admin:accounts_user_change", args=[staff.pk]))
    html = response.content.decode()
    assert 'name="role"' not in html
    assert 'name="is_active"' not in html


def ok_view(request):
    return HttpResponse("ok")


@pytest.mark.parametrize(
    ("decorator", "user_fixture", "expected"),
    [
        (staff_required, "staff", 200),
        (staff_required, "shopper", 404),
        (shopper_required, "shopper", 200),
        (shopper_required, "staff", 404),
    ],
)
def test_role_decorators(request, decorator, user_fixture, expected):
    req = RequestFactory().get("/somewhere/")
    req.user = request.getfixturevalue(user_fixture)
    view = decorator(ok_view)
    if expected == 404:
        from django.http import Http404

        with pytest.raises(Http404):
            view(req)
    else:
        assert view(req).status_code == 200


def test_role_decorators_send_signed_out_users_to_sign_in():
    from django.contrib.auth.models import AnonymousUser

    req = RequestFactory().get("/somewhere/")
    req.user = AnonymousUser()
    response = staff_required(ok_view)(req)
    assert response.status_code == 302
    assert response["Location"].startswith(reverse("accounts:login"))


# --- Bootstrap command -------------------------------------------------------------


def test_create_staff_user_prints_a_working_link(client, capsys):
    call_command("create_staff_user", "James@Example.com", "--name", "James")
    out = capsys.readouterr().out
    user = User.objects.get(email="james@example.com")
    assert user.is_staff
    path = LINK.search(out).group().replace("http://localhost:8000", "")
    client.post(path)
    assert client.session["_auth_user_id"] == str(user.pk)


def test_create_staff_user_promotes_an_existing_shopper(shopper, capsys):
    call_command("create_staff_user", shopper.email)
    shopper.refresh_from_db()
    assert shopper.role == User.Role.STAFF
