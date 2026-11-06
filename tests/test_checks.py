from apps.core.checks import email_configured

BREVO = {"default": {"BACKEND": "anymail.backends.brevo.EmailBackend"}}


def test_no_warning_for_non_anymail_backends(settings):
    assert email_configured(None) == []


def test_warns_when_production_email_is_missing(settings):
    settings.MAILERS = BREVO
    settings.DEFAULT_FROM_EMAIL = ""
    settings.ANYMAIL = {"BREVO_API_KEY": ""}
    [warning] = email_configured(None)
    assert warning.id == "core.W001"
    assert "DEFAULT_FROM_EMAIL and BREVO_API_KEY" in warning.hint


def test_no_warning_when_production_email_is_configured(settings):
    settings.MAILERS = BREVO
    settings.DEFAULT_FROM_EMAIL = "Customer Wise <no-reply@example.com>"
    settings.ANYMAIL = {"BREVO_API_KEY": "key"}
    assert email_configured(None) == []
