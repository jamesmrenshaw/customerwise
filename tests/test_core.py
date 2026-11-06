import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db


def test_healthz_reports_ok(client):
    response = client.get(reverse("core:healthz"))
    assert response.status_code == 200
    assert response.content == b"ok"
    assert "no-cache" in response["Cache-Control"]


def test_healthz_rejects_post(client):
    assert client.post(reverse("core:healthz")).status_code == 405


def test_home_uses_brand_layout(client):
    response = client.get(reverse("core:home"))
    assert response.status_code == 200
    html = response.content.decode()
    assert 'alt="Customer Wise"' in html
    assert "css/tokens.css" in html


def test_responses_carry_strict_security_headers(client):
    response = client.get(reverse("core:home"))
    csp = response["Content-Security-Policy"]
    assert "default-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "unsafe-inline" not in csp
    assert response["X-Frame-Options"] == "DENY"
