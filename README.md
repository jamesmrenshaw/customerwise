# Customer Wise

Shopper workspace pilot for Customer Wise B2B mystery shopping. Django + HTMX + PostgreSQL, deployed on Railway.

- Architecture and build order: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- Stories and decisions: Linear, project "Customer Wise B2B Platform", milestone "MVP Shopper workspace pilot"
- Approved UX prototype: https://claude.ai/artifact/UxrMxGDUQc3e4wDMaDY47o

## Local development

You need [uv](https://docs.astral.sh/uv/) and a Postgres 17 database. The schema uses Postgres-only features, so SQLite is not supported.

```sh
# Postgres in a container (podman or docker)
podman run -d --name cw-postgres -p 5432:5432 \
  -e POSTGRES_USER=customerwise -e POSTGRES_PASSWORD=customerwise -e POSTGRES_DB=customerwise \
  docker.io/library/postgres:17

uv sync                              # installs Python 3.13 and dependencies
cp .env.example .env
uv run python manage.py migrate
uv run python manage.py runserver    # http://localhost:8000
```

Checks (the same ones CI runs):

```sh
uv run ruff check . && uv run ruff format --check .
uv run pytest
```

## Settings

| Module | Used for |
|---|---|
| `config.settings.dev` | local development (`manage.py` default) |
| `config.settings.test` | pytest |
| `config.settings.prod` | Railway dev and production environments (`wsgi.py` default) |

## Deployment

Railway builds the `Dockerfile` and runs `migrate` before each deploy (`railway.json`). The health check is `/healthz`. Each environment needs Postgres, a bucket and the variables listed in `.env.example`.
