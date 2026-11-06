from django.core.mail.backends.base import BaseEmailBackend


class EmailBackend(BaseEmailBackend):
    """A mail backend that always fails, for testing error handling."""

    def send_messages(self, email_messages):
        raise ConnectionError("provider unavailable")
