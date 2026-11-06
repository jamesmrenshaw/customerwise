"""Production and Railway dev-environment settings. All secrets come from environment variables."""

from .base import *  # noqa: F403
from .base import env

DEBUG = False
SECRET_KEY = env("DJANGO_SECRET_KEY")

# Railway's health check calls the service with this Host header.
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=[]) + ["healthcheck.railway.app"]
CSRF_TRUSTED_ORIGINS = env.list("DJANGO_CSRF_TRUSTED_ORIGINS", default=[])

# Railway terminates TLS at its proxy.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = True
SECURE_REDIRECT_EXEMPT = [r"^healthz$"]  # internal health check is plain HTTP
SECURE_HSTS_SECONDS = 60 * 60 * 24 * 365
# Covers only subdomains of our own hostname, never other railway.app services.
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
# Preload needs a domain we own; not possible on a railway.app subdomain. Revisit with a custom domain.
SILENCED_SYSTEM_CHECKS = ["security.W021"]
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

# Transactional email through Brevo (D02): invitations and magic links.
INSTALLED_APPS = [*INSTALLED_APPS, "anymail"]  # noqa: F405
MAILERS = {"default": {"BACKEND": "anymail.backends.brevo.EmailBackend"}}
ANYMAIL = {"BREVO_API_KEY": env("BREVO_API_KEY", default="")}
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL")
SERVER_EMAIL = DEFAULT_FROM_EMAIL

# Private Railway bucket (S3-compatible). Files are only reachable through
# presigned links that expire after 5 minutes. Defaults allow the Docker build
# (collectstatic) to import settings without secrets.
STORAGES = {
    **STORAGES,  # noqa: F405
    "default": {"BACKEND": "storages.backends.s3.S3Storage"},
}
AWS_STORAGE_BUCKET_NAME = env("BUCKET_NAME", default="")
AWS_S3_ENDPOINT_URL = env("BUCKET_ENDPOINT_URL", default="")
AWS_S3_REGION_NAME = env("BUCKET_REGION", default="auto")
AWS_ACCESS_KEY_ID = env("BUCKET_ACCESS_KEY_ID", default="")
AWS_SECRET_ACCESS_KEY = env("BUCKET_SECRET_ACCESS_KEY", default="")
AWS_DEFAULT_ACL = None
AWS_QUERYSTRING_AUTH = True
AWS_QUERYSTRING_EXPIRE = 300
AWS_S3_FILE_OVERWRITE = False
AWS_S3_ADDRESSING_STYLE = env("BUCKET_ADDRESSING_STYLE", default="virtual")
