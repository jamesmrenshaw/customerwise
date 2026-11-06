"""Branded emails: an HTML version with the Customer Wise logo, plus a plain-text alternative.

Each email has two templates with the same base name: `<name>.txt` for the text part and
`<name>.html` (extending `emails/base.html`) for the HTML part.

The logo is loaded from the site rather than embedded, because Brevo's API doesn't accept inline
(CID) images. It is a PNG because Outlook can't show WebP. The unhashed static path is used so
emails already sent keep working after the logo file changes.
"""

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string

LOGO_PATH = "img/email-logo.png"


def logo_url():
    return settings.SITE_URL.rstrip("/") + "/" + settings.STATIC_URL.strip("/") + "/" + LOGO_PATH


def send_email(to, subject, template, context):
    """Render `template` (without extension) as text and HTML and send it to one address."""
    context = {**context, "logo_url": logo_url(), "subject": subject}
    text = render_to_string(f"{template}.txt", context)
    html = render_to_string(f"{template}.html", context)
    message = EmailMultiAlternatives(subject, text, None, [to])
    message.attach_alternative(html, "text/html")
    message.send()
