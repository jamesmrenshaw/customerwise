import pytest
from django.db import IntegrityError

from apps.accounts.models import User

pytestmark = pytest.mark.django_db


def test_new_users_are_shoppers_without_passwords():
    user = User.objects.create_user("  Sam.Okafor@Example.com ")
    assert user.email == "sam.okafor@example.com"
    assert user.role == User.Role.SHOPPER
    assert not user.has_usable_password()


def test_email_is_unique_regardless_of_case():
    User.objects.create_user("sam@example.com")
    with pytest.raises(IntegrityError):
        User.objects.create_user("SAM@example.com")


def test_shoppers_never_get_admin_access_or_permissions():
    shopper = User.objects.create_user("shopper@example.com", is_superuser=True)
    shopper.refresh_from_db()
    assert not shopper.is_staff
    assert not shopper.is_superuser
    assert not shopper.has_perm("accounts.change_user")
    assert not shopper.has_module_perms("accounts")


def test_all_active_staff_have_full_admin_permissions():
    staff = User.objects.create_staff("alex@customerwise.example")
    assert staff.is_staff
    assert staff.has_perm("accounts.change_user")
    assert staff.has_module_perms("accounts")


def test_deactivated_staff_lose_admin_access():
    staff = User.objects.create_staff("alex@customerwise.example", is_active=False)
    assert not staff.is_staff
    assert not staff.has_perm("accounts.change_user")


def test_shopper_cannot_open_admin(client):
    shopper = User.objects.create_user("shopper@example.com")
    client.force_login(shopper)
    response = client.get("/admin/")
    assert response.status_code == 302
    assert "/admin/login/" in response["Location"]


def test_staff_can_open_admin(client):
    staff = User.objects.create_staff("alex@customerwise.example")
    client.force_login(staff)
    assert client.get("/admin/accounts/user/").status_code == 200


def test_deactivated_user_is_signed_out_on_next_request(client):
    staff = User.objects.create_staff("alex@customerwise.example")
    client.force_login(staff)
    staff.is_active = False
    staff.save()
    response = client.get("/admin/")
    assert response.status_code == 302
