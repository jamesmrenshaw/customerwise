# Customer Wise web service (Railway). See docs/ARCHITECTURE.md.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    DJANGO_SETTINGS_MODULE=config.settings.prod

# System libraries for WeasyPrint (PDF export, CUS-142) plus a fallback font, and the PostgreSQL 18
# client for nightly backups (pg_dump must be at least the server's version, so it comes from the
# PostgreSQL project's repository rather than Debian's). The repository's signing key is kept in the
# repo (docker/apt.postgresql.org.asc, fingerprint B97B0AFCAA1A47F044F244A07FCC7D46ACCC4CF8) so
# builds don't depend on downloading it; apt retries flaky downloads.
COPY docker/apt.postgresql.org.asc /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc
RUN echo 'Acquire::Retries "5";' > /etc/apt/apt.conf.d/80-retries \
    && . /etc/os-release \
    && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt ${VERSION_CODENAME}-pgdg main" > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
       libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 fonts-dejavu-core postgresql-client-18 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /bin/uv

WORKDIR /app

# Install dependencies first so they are cached between code changes.
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --locked --no-dev --no-install-project

COPY . .
RUN DJANGO_SECRET_KEY=build-only-not-secret python manage.py collectstatic --noinput

RUN useradd --create-home --uid 1000 app && chown -R app /app
USER app

EXPOSE 8000
# Migrations run here rather than as a Railway pre-deploy step, so deploys don't depend on Railway
# config (there is no railway.json). With one replica per environment this is safe and a no-op
# when up to date. If migrate fails, the container exits and the deploy fails.
# exec makes gunicorn PID 1 so it receives SIGTERM from Railway and shuts down gracefully.
CMD ["sh", "-c", "python manage.py migrate --noinput && exec gunicorn config.wsgi --bind 0.0.0.0:${PORT:-8000} --workers ${WEB_CONCURRENCY:-2} --timeout 120 --access-logfile - --forwarded-allow-ips='*'"]
