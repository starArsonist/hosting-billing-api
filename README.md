# hosting-billing-api

Асинхронный REST API для учёта размещённого оборудования: устройства, ежедневная телеметрия,
аудируемые корректировки и счета, которые остаются корректными при смене цен и арендаторов.

Python 3.11 · FastAPI · SQLAlchemy 2.0 (async) · Alembic · PostgreSQL / SQLite · pytest

> Портфолио-проект. Это переработка «с чистого листа» сервиса, над которым я работал в
> коммерческой компании; все названия, данные и код здесь оригинальные и вымышленные.

## Что умеет

- **Устройства** с моделями, владельцами, арендаторами (клиентами, арендующими устройство) и статусом.
- **Ежедневная телеметрия** по каждому устройству: аптайм, энергопотребление (кВт·ч), хешрейт (TH/s).
- **Корректировки (overrides)**: исправление никогда не уничтожает оригинал. Каждую метрику
  можно корректировать независимо (`null` = оставить оригинал); при чтении возвращаются оба значения.
- **Биллинг**, который считается посуточно:
  - берётся цена, действовавшая *в этот день* (история тарифов, а не «текущая цена»);
  - день выставляется тому, кто арендовал устройство *в этот день* (история арендаторов);
  - подряд идущие дни с одной ценой группируются в сегменты для счёта;
  - деньги хранятся как `Decimal`, округление half-up выполняется по сегментам.
- **Массовый импорт** (`/stats/bulk`, `/stats/overrides/bulk`): идемпотентный upsert по ключу
  (устройство, день); устройства сопоставляются по серийному номеру или внешнему id; неизвестные
  устройства попадают в отчёт, а не приводят к ошибке; отсутствующие метрики не перезаписывают сохранённые.
- **Матричное представление** с фильтрами и keyset-пагинацией (по курсору), устойчивой к вставкам.

## Архитектура

```
presentation  routers, pydantic schemas      (HTTP only)
application   services, one commit per use case
domain        billing.py, stats.py           (pure Python, no I/O, fully unit-tested)
infrastructure  SQLAlchemy models, repositories, session
```

Ключевые решения:

- Расчёт биллинга живёт в чистом доменном слое, поэтому правила тестируются без базы данных.
- У ORM-моделей нет relationship: это исключает неявные ленивые загрузки (`MissingGreenlet`) в асинхронном коде.
- Upsert не зависит от диалекта (`ON CONFLICT DO UPDATE` с `coalesce`) и работает и в SQLite, и в PostgreSQL.
- Удаления каскадируются на уровне базы данных.
- Опциональная аутентификация по API-ключу через `X-API-Key` (`/health` открыт).

## Запуск

```bash
cp .env.example .env
docker compose up --build        # PostgreSQL + API on http://127.0.0.1:8000/docs
```

Без Docker:

```bash
uv sync --group dev
uv run alembic upgrade head
uv run python scripts/seed.py    # optional demo data
uv run uvicorn hosting_billing.main:app_factory --factory --reload
```

## Пример

```bash
curl -X POST localhost:8000/clients -H 'content-type: application/json' \
  -d '{"name":"Acme","price_per_kwh":"5.00"}'
curl -X POST localhost:8000/clients/1/tariffs -H 'content-type: application/json' \
  -d '{"price_per_kwh":"6.00","effective_from":"2026-02-01"}'
curl 'localhost:8000/billing/client-report?client_id=1&date_from=2026-01-01&date_to=2026-02-28'
```

## Тесты

```bash
uv run pytest                                         # SQLite
TEST_DATABASE_URL=postgresql+asyncpg://... uv run pytest   # PostgreSQL
```

`tests/unit` покрывает правила биллинга и схемы; `tests/integration` прогоняет HTTP API
через `httpx.ASGITransport` на реальной базе данных.

## Лицензия

MIT
