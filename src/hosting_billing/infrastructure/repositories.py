"""Data access. The only module that knows SQL; services above it never build queries."""

from __future__ import annotations

import base64
import binascii
import datetime as dt
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from hosting_billing.domain.stats import FIELDS

from .db.models import (
    Client,
    DailyStat,
    Device,
    DeviceModel,
    StatOverride,
    TariffChange,
    TenantAssignment,
    utcnow,
)


def _dialect_insert(session: AsyncSession):
    """Return the ``insert`` construct that supports ON CONFLICT for the active database."""
    name = session.get_bind().dialect.name
    if name == "postgresql":
        return postgresql.insert
    if name == "sqlite":
        return sqlite.insert
    raise NotImplementedError(f"bulk upsert is not implemented for dialect {name!r}")


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# ------------------------------------------------------------------------------ clients


class ClientRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, name: str, contact: str | None, base_price: Decimal) -> Client:
        client = Client(name=name, contact=contact, base_price=base_price)
        self.session.add(client)
        await self.session.flush()
        return client

    async def get(self, client_id: int) -> Client | None:
        return await self.session.get(Client, client_id)

    async def list(self) -> Sequence[Client]:
        return (await self.session.scalars(select(Client).order_by(Client.id))).all()

    async def tariff_changes(self, client_id: int) -> Sequence[TariffChange]:
        stmt = (
            select(TariffChange)
            .where(TariffChange.client_id == client_id)
            .order_by(TariffChange.effective_from)
        )
        return (await self.session.scalars(stmt)).all()

    async def all_tariff_changes(self) -> dict[int, list[TariffChange]]:
        grouped: dict[int, list[TariffChange]] = defaultdict(list)
        stmt = select(TariffChange).order_by(TariffChange.effective_from)
        for change in (await self.session.scalars(stmt)).all():
            grouped[change.client_id].append(change)
        return grouped

    async def tariff_change_exists(self, client_id: int, day: dt.date) -> bool:
        stmt = select(TariffChange.id).where(
            TariffChange.client_id == client_id, TariffChange.effective_from == day
        )
        return (await self.session.scalar(stmt)) is not None

    async def add_tariff_change(
        self, client_id: int, day: dt.date, price: Decimal
    ) -> TariffChange:
        change = TariffChange(client_id=client_id, effective_from=day, price_per_kwh=price)
        self.session.add(change)
        await self.session.flush()
        return change


# ------------------------------------------------------------------------------ devices


class DeviceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add_model(self, brand: str, model: str) -> DeviceModel:
        row = DeviceModel(brand=brand, model=model)
        self.session.add(row)
        await self.session.flush()
        return row

    async def get_model(self, model_id: int) -> DeviceModel | None:
        return await self.session.get(DeviceModel, model_id)

    async def find_model(self, brand: str, model: str) -> DeviceModel | None:
        stmt = select(DeviceModel).where(DeviceModel.brand == brand, DeviceModel.model == model)
        return await self.session.scalar(stmt)

    async def list_models(self) -> Sequence[DeviceModel]:
        stmt = select(DeviceModel).order_by(DeviceModel.brand, DeviceModel.model)
        return (await self.session.scalars(stmt)).all()

    async def add(self, device: Device) -> Device:
        self.session.add(device)
        await self.session.flush()
        return device

    async def get(self, device_id: int) -> Device | None:
        return await self.session.get(Device, device_id)

    async def exists_with(self, external_id: str | None, serial: str | None) -> str | None:
        """Return the name of the clashing unique field, if any."""
        if external_id is not None:
            stmt = select(Device.id).where(Device.external_id == external_id)
            if await self.session.scalar(stmt) is not None:
                return "external_id"
        if serial is not None:
            stmt = select(Device.id).where(Device.serial_number == serial)
            if await self.session.scalar(stmt) is not None:
                return "serial_number"
        return None

    async def list(
        self,
        *,
        status: str | None,
        owner_id: int | None,
        tenant_id: int | None,
        search: str | None,
        limit: int,
        offset: int,
    ) -> Sequence[Device]:
        stmt = select(Device).order_by(Device.id).limit(limit).offset(offset)
        if status:
            stmt = stmt.where(Device.status == status)
        if owner_id is not None:
            stmt = stmt.where(Device.owner_id == owner_id)
        if tenant_id is not None:
            stmt = stmt.where(Device.tenant_id == tenant_id)
        if search:
            pattern = f"%{_escape_like(search)}%"
            stmt = stmt.where(
                or_(
                    Device.serial_number.ilike(pattern, escape="\\"),
                    Device.external_id.ilike(pattern, escape="\\"),
                )
            )
        return (await self.session.scalars(stmt)).all()

    async def delete(self, device_id: int) -> None:
        # stats, overrides and assignments go away through ON DELETE CASCADE
        await self.session.execute(delete(Device).where(Device.id == device_id))

    async def assignments(self, device_id: int) -> Sequence[TenantAssignment]:
        stmt = (
            select(TenantAssignment)
            .where(TenantAssignment.device_id == device_id)
            .order_by(TenantAssignment.effective_from)
        )
        return (await self.session.scalars(stmt)).all()

    async def add_assignment(
        self, device_id: int, tenant_id: int, effective_from: dt.date
    ) -> TenantAssignment:
        row = TenantAssignment(
            device_id=device_id, tenant_id=tenant_id, effective_from=effective_from
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def resolve(
        self, serials: Iterable[str], external_ids: Iterable[str]
    ) -> tuple[dict[str, int], dict[str, int]]:
        """Map serial numbers and external ids to device ids in a single query."""
        serials, external_ids = set(serials), set(external_ids)
        by_serial: dict[str, int] = {}
        by_external: dict[str, int] = {}
        if not serials and not external_ids:
            return by_serial, by_external
        conditions = []
        if serials:
            conditions.append(Device.serial_number.in_(serials))
        if external_ids:
            conditions.append(Device.external_id.in_(external_ids))
        stmt = select(Device.id, Device.serial_number, Device.external_id).where(or_(*conditions))
        for device_id, serial, external in (await self.session.execute(stmt)).all():
            if serial:
                by_serial[serial] = device_id
            by_external[external] = device_id
        return by_serial, by_external


# -------------------------------------------------------------------------------- stats


def encode_cursor(device_id: int, day: dt.date) -> str:
    raw = f"{device_id}:{day.isoformat()}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[int, dt.date]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        device_part, day_part = base64.urlsafe_b64decode(padded).decode().split(":", 1)
        return int(device_part), dt.date.fromisoformat(day_part)
    except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
        raise ValueError("invalid cursor") from exc


@dataclass(frozen=True)
class MatrixFilters:
    date_from: dt.date | None = None
    date_to: dt.date | None = None
    owner_id: int | None = None
    tenant_id: int | None = None
    status: str | None = None
    model: str | None = None
    serial: str | None = None


class StatsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ---- single records

    async def get_stat(self, device_id: int, day: dt.date) -> DailyStat | None:
        stmt = select(DailyStat).where(DailyStat.device_id == device_id, DailyStat.day == day)
        return await self.session.scalar(stmt)

    async def get_override(self, stat_id: int) -> StatOverride | None:
        stmt = select(StatOverride).where(StatOverride.stat_id == stat_id)
        return await self.session.scalar(stmt)

    async def put_stat(self, device_id: int, day: dt.date, values: dict) -> DailyStat:
        """Create or fully replace one day's telemetry."""
        stat = await self.get_stat(device_id, day)
        if stat is None:
            stat = DailyStat(device_id=device_id, day=day, **values)
            self.session.add(stat)
        else:
            for name in FIELDS:
                setattr(stat, name, values.get(name))
        await self.session.flush()
        return stat

    async def put_override(self, stat_id: int, values: dict, comment: str | None) -> StatOverride:
        override = await self.get_override(stat_id)
        if override is None:
            override = StatOverride(stat_id=stat_id, comment=comment, **values)
            self.session.add(override)
        else:
            for name in FIELDS:
                setattr(override, name, values.get(name))
            override.comment = comment
            override.updated_at = utcnow()
        await self.session.flush()
        return override

    async def delete_override(self, stat_id: int) -> bool:
        result = await self.session.execute(
            delete(StatOverride).where(StatOverride.stat_id == stat_id)
        )
        return bool(result.rowcount)

    # ---- bulk

    async def bulk_upsert_stats(self, rows: list[dict]) -> None:
        """Insert many daily stats, updating on conflict.

        ``COALESCE(incoming, existing)`` means a row that carries only some metrics (for
        instance an energy-only import) enriches the record instead of wiping the others.
        Rows must be unique per (device_id, day): PostgreSQL refuses to touch a row twice.
        """
        if not rows:
            return
        insert = _dialect_insert(self.session)
        stmt = insert(DailyStat).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=["device_id", "day"],
            set_={
                name: func.coalesce(getattr(stmt.excluded, name), getattr(DailyStat, name))
                for name in FIELDS
            },
        )
        await self.session.execute(stmt)

    async def ensure_stats_exist(self, rows: list[dict]) -> dict[tuple[int, dt.date], int]:
        """Create missing stat rows (keeping existing ones) and return their ids."""
        if not rows:
            return {}
        insert = _dialect_insert(self.session)
        stmt = insert(DailyStat).values(rows).on_conflict_do_nothing(
            index_elements=["device_id", "day"]
        )
        await self.session.execute(stmt)

        keys = [(r["device_id"], r["day"]) for r in rows]
        device_ids = sorted({k[0] for k in keys})
        days = sorted({k[1] for k in keys})
        stmt = select(DailyStat.id, DailyStat.device_id, DailyStat.day).where(
            DailyStat.device_id.in_(device_ids), DailyStat.day.in_(days)
        )
        wanted = set(keys)
        found = {}
        for stat_id, device_id, day in (await self.session.execute(stmt)).all():
            if (device_id, day) in wanted:
                found[(device_id, day)] = stat_id
        return found

    async def bulk_upsert_overrides(self, rows: list[dict]) -> None:
        if not rows:
            return
        insert = _dialect_insert(self.session)
        stmt = insert(StatOverride).values(rows)
        set_ = {
            name: func.coalesce(getattr(stmt.excluded, name), getattr(StatOverride, name))
            for name in FIELDS
        }
        set_["comment"] = func.coalesce(stmt.excluded.comment, StatOverride.comment)
        set_["updated_at"] = utcnow()
        stmt = stmt.on_conflict_do_update(index_elements=["stat_id"], set_=set_)
        await self.session.execute(stmt)

    # ---- matrix

    def _matrix_query(self, columns, filters: MatrixFilters):
        owner, tenant = aliased(Client), aliased(Client)
        stmt = (
            select(*columns(owner, tenant))
            .select_from(DailyStat)
            .join(Device, Device.id == DailyStat.device_id)
            .join(DeviceModel, DeviceModel.id == Device.device_model_id)
            .join(owner, owner.id == Device.owner_id)
            .join(tenant, tenant.id == Device.tenant_id)
            .outerjoin(StatOverride, StatOverride.stat_id == DailyStat.id)
        )
        if filters.date_from:
            stmt = stmt.where(DailyStat.day >= filters.date_from)
        if filters.date_to:
            stmt = stmt.where(DailyStat.day <= filters.date_to)
        if filters.owner_id is not None:
            stmt = stmt.where(Device.owner_id == filters.owner_id)
        if filters.tenant_id is not None:
            stmt = stmt.where(Device.tenant_id == filters.tenant_id)
        if filters.status:
            stmt = stmt.where(Device.status == filters.status)
        if filters.model:
            stmt = stmt.where(DeviceModel.model.icontains(filters.model, autoescape=True))
        if filters.serial:
            stmt = stmt.where(Device.serial_number.icontains(filters.serial, autoescape=True))
        return stmt

    async def matrix_page(
        self, filters: MatrixFilters, *, limit: int, cursor: str | None
    ) -> tuple[list, str | None]:
        """Keyset-paginated rows ordered by (device_id ASC, day DESC).

        Unlike OFFSET paging the cost of a page does not grow with its depth, and rows
        inserted while a client is paging cannot shift the window and cause duplicates.
        """

        def columns(owner, tenant):
            return (
                DailyStat.id.label("stat_id"),
                DailyStat.device_id,
                DailyStat.day,
                DailyStat.uptime_hours.label("orig_uptime"),
                DailyStat.consumption_kwh.label("orig_kwh"),
                DailyStat.hashrate_ths.label("orig_hashrate"),
                StatOverride.id.label("override_id"),
                StatOverride.uptime_hours.label("ov_uptime"),
                StatOverride.consumption_kwh.label("ov_kwh"),
                StatOverride.hashrate_ths.label("ov_hashrate"),
                Device.serial_number,
                Device.ip_address,
                DeviceModel.brand,
                DeviceModel.model,
                owner.name.label("owner_name"),
                tenant.name.label("tenant_name"),
            )

        stmt = self._matrix_query(columns, filters)
        if cursor:
            last_device, last_day = decode_cursor(cursor)
            stmt = stmt.where(
                or_(
                    DailyStat.device_id > last_device,
                    and_(DailyStat.device_id == last_device, DailyStat.day < last_day),
                )
            )
        stmt = stmt.order_by(DailyStat.device_id.asc(), DailyStat.day.desc()).limit(limit + 1)

        rows = (await self.session.execute(stmt)).all()
        has_more = len(rows) > limit
        rows = rows[:limit]
        next_cursor = encode_cursor(rows[-1].device_id, rows[-1].day) if has_more else None
        return rows, next_cursor

    async def matrix_count(self, filters: MatrixFilters) -> int:
        stmt = self._matrix_query(lambda owner, tenant: (func.count(DailyStat.id),), filters)
        return int(await self.session.scalar(stmt) or 0)

    # ---- billing inputs

    async def devices_rented_by(self, client_id: int) -> list:
        rented = select(TenantAssignment.device_id).where(TenantAssignment.tenant_id == client_id)
        stmt = (
            select(Device.id, Device.serial_number, DeviceModel.brand, DeviceModel.model)
            .join(DeviceModel, DeviceModel.id == Device.device_model_id)
            .where(Device.id.in_(rented))
            .order_by(Device.id)
        )
        return list((await self.session.execute(stmt)).all())

    async def assignments_for(self, device_ids: Sequence[int]) -> dict[int, list]:
        grouped: dict[int, list] = defaultdict(list)
        if not device_ids:
            return grouped
        stmt = select(TenantAssignment).where(TenantAssignment.device_id.in_(device_ids))
        for row in (await self.session.scalars(stmt)).all():
            grouped[row.device_id].append(row)
        return grouped

    async def effective_consumption(
        self, device_ids: Sequence[int], date_from: dt.date, date_to: dt.date
    ) -> dict[int, list[tuple[dt.date, float]]]:
        """Per-day consumption with overrides applied; days without a reading are omitted."""
        grouped: dict[int, list[tuple[dt.date, float]]] = defaultdict(list)
        if not device_ids:
            return grouped
        stmt = (
            select(
                DailyStat.device_id,
                DailyStat.day,
                DailyStat.consumption_kwh.label("original_kwh"),
                StatOverride.consumption_kwh.label("override_kwh"),
            )
            .outerjoin(StatOverride, StatOverride.stat_id == DailyStat.id)
            .where(
                DailyStat.device_id.in_(device_ids),
                DailyStat.day >= date_from,
                DailyStat.day <= date_to,
            )
        )
        for device_id, day, original, override in (await self.session.execute(stmt)).all():
            value = override if override is not None else original
            if value is not None:
                grouped[device_id].append((day, value))
        return grouped
