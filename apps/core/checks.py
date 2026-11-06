from django.conf import settings
from django.core.checks import Warning, register


@register()
def email_configured(app_configs, **kwargs):
    """Warn (rather than fail to start) when production email is not set up.

    Runs on every management command, so it shows in Railway's pre-deploy migrate logs.
    """
    backend = settings.MAILERS.get("default", {}).get("BACKEND", "")
    if "anymail" not in backend:
        return []
    missing = []
    if not settings.DEFAULT_FROM_EMAIL:
        missing.append("DEFAULT_FROM_EMAIL")
    if not getattr(settings, "ANYMAIL", {}).get("BREVO_API_KEY"):
        missing.append("BREVO_API_KEY")
    if not missing:
        return []
    return [
        Warning(
            "Email is not configured, so invitations and sign-in links cannot be sent.",
            hint=f"Set {' and '.join(missing)} on the Railway service.",
            id="core.W001",
        )
    ]
