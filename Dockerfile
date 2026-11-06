# Customer Wise web service (Railway). See docs/ARCHITECTURE.md.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    DJANGO_SETTINGS_MODULE=config.settings.prod

# System libraries for WeasyPrint (PDF export) are added with CUS-142.

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
# exec makes gunicorn PID 1 so it receives SIGTERM from Railway and shuts down gracefully.
CMD ["sh", "-c", "exec gunicorn config.wsgi --bind 0.0.0.0:${PORT:-8000} --workers ${WEB_CONCURRENCY:-2} --access-logfile - --forwarded-allow-ips='*'"]
