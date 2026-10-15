# hosting-billing-api

Async REST API for accounting of hosted equipment: devices, daily telemetry,
auditable corrections and invoices that stay correct when prices and tenants change.

Python 3.11 · FastAPI · SQLAlchemy 2.0 (async) · Alembic · PostgreSQL / SQLite · pytest

> Portfolio project. It is a clean-room redesign of a service I worked on in a
> commercial setting; all names, data and code here are original and fictional.

## What it does

- **Devices** with models, owners, tenants (clients renting the device) and status.
- **Daily telemetry** per device: uptime, energy (kWh), hashrate (TH/s).
- **Overrides**: a correction never destroys the original. Each metric can be corrected
  independently (`null` = keep the original); reads return both values.
- **Billing** that is computed per day:
  - the price is the one valid *on that day* (tariff history, not "current price");
  - a day is billed to whoever rented the device *on that day* (tenant history);
  - consecutive days at the same price are grouped into segments for the invoice;
  - money is `Decimal`, rounded half-up per segment.
- **Bulk import** (`/stats/bulk`, `/stats/overrides/bulk`): idempotent upsert keyed by
  (device, day); devices matched by serial or external id; unknown devices are reported,
  not fatal; absent metrics do not overwrite stored ones.
- **Matrix view** with filters and keyset (cursor) pagination, stable under inserts.

## Design

```
presentation  routers, pydantic schemas      (HTTP only)
application   services, one commit per use case
domain        billing.py, stats.py           (pure Python, no I/O, fully unit-tested)
infrastructure  SQLAlchemy models, repositories, session
```

Notable choices:

- Billing math lives in the pure domain layer, so the rules are testable without a database.
- ORM models have no relationships: avoids implicit lazy loads (`MissingGreenlet`) in async code.
- Upsert is dialect-agnostic (`ON CONFLICT DO UPDATE` with `coalesce`), works on SQLite and PostgreSQL.
- Deletes cascade at the database level.
- Optional API-key auth via `X-API-Key` (`/health` is open).

## Run

```bash
cp .env.example .env
docker compose up --build        # PostgreSQL + API on http://127.0.0.1:8000/docs
```

Without Docker:

```bash
uv sync --group dev
uv run alembic upgrade head
uv run python scripts/seed.py    # optional demo data
uv run uvicorn hosting_billing.main:app_factory --factory --reload
```

## Example

```bash
curl -X POST localhost:8000/clients -H 'content-type: application/json' \
  -d '{"name":"Acme","price_per_kwh":"5.00"}'
curl -X POST localhost:8000/clients/1/tariffs -H 'content-type: application/json' \
  -d '{"price_per_kwh":"6.00","effective_from":"2026-02-01"}'
curl 'localhost:8000/billing/client-report?client_id=1&date_from=2026-01-01&date_to=2026-02-28'
```

## Tests

```bash
uv run pytest                                         # SQLite
TEST_DATABASE_URL=postgresql+asyncpg://... uv run pytest   # PostgreSQL
```

`tests/unit` covers the billing and schema rules; `tests/integration` drives the HTTP API
through `httpx.ASGITransport` against a real database.

## License

MIT
