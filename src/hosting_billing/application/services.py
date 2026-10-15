"""Use cases. Each public method is one transaction: it commits once at the end, and any
exception leaves the session to be rolled back when it is closed."""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import TypeVar

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from hosting_billing.domain import (
    Assignment,
    ClientReport,
    DeviceUsage,
    TariffPoint,
    apply_override,
    build_client_report,
    price_on,
)
from hosting_billing.domain.stats import FIELDS, EffectiveStat
from hosting_billing.infrastructure.db.models import Client, Device, TariffChange
from hosting_billing.infrastructure.repositories import (
    ClientRepository,
    DeviceRepository,
    MatrixFilters,
    StatsRepository,
)

from .errors import ConflictError, InvalidRequestError, NotFoundError

T = TypeVar("T")
BULK_BATCH_SIZE = 500
MAX_REPORT_DAYS = 400


def _chunks(items: Sequence[T], size: int) -> Iterable[Sequence[T]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _points(changes: Iterable[TariffChange]) -> list[TariffPoint]:
    return [TariffPoint(c.effective_from, c.price_per_kwh) for c in changes]


# ------------------------------------------------------------------------------ clients


class ClientService:
    def __init__(self, session: AsyncSession, today: Callable[[], dt.date] = dt.date.today):
        self.session = session
        self.repo = ClientRepository(session)
        self.today = today

    async def create(self, *, name: str, contact: str | None, price_per_kwh: Decimal) -> Client:
        client = await self.repo.add(name, contact, price_per_kwh)
        await self.session.commit()
        return client

    async def get(self, client_id: int) -> Client:
        client = await self.repo.get(client_id)
        if client is None:
            raise NotFoundError("client", client_id)
        return client

    async def current_price(self, client: Client) -> Decimal:
        changes = _points(await self.repo.tariff_changes(client.id))
        return price_on(client.base_price, changes, self.today())

    async def list_with_prices(self) -> list[tuple[Client, Decimal]]:
        by_client = await self.repo.all_tariff_changes()
        today = self.today()
        return [
            (c, price_on(c.base_price, _points(by_client.get(c.id, [])), today))
            for c in await self.repo.list()
        ]

    async def update(self, client_id: int, *, name: str | None, contact: str | None) -> Client:
        client = await self.get(client_id)
        if name is not None:
            client.name = name
        if contact is not None:
            client.contact = contact
        await self.session.commit()
        return client

    async def change_tariff(
        self, client_id: int, *, price_per_kwh: Decimal, effective_from: dt.date | None
    ) -> TariffChange:
        await self.get(client_id)
        day = effective_from or self.today()
        if await self.repo.tariff_change_exists(client_id, day):
            raise ConflictError(f"a tariff change effective {day.isoformat()} already exists")
        change = await self.repo.add_tariff_change(client_id, day, price_per_kwh)
        await self.session.commit()
        return change

    async def tariff_history(self, client_id: int) -> tuple[Client, Sequence[TariffChange]]:
        client = await self.get(client_id)
        return client, await self.repo.tariff_changes(client_id)


# ------------------------------------------------------------------------------ devices


class DeviceService:
    def __init__(self, session: AsyncSession, today: Callable[[], dt.date] = dt.date.today):
        self.session = session
        self.repo = DeviceRepository(session)
        self.clients = ClientRepository(session)
        self.today = today

    async def create_model(self, *, brand: str, model: str):
        if await self.repo.find_model(brand, model) is not None:
            raise ConflictError(f"model {brand} {model} already exists")
        row = await self.repo.add_model(brand, model)
        await self.session.commit()
        return row

    async def list_models(self):
        return await self.repo.list_models()

    async def get(self, device_id: int) -> Device:
        device = await self.repo.get(device_id)
        if device is None:
            raise NotFoundError("device", device_id)
        return device

    async def _require_client(self, client_id: int) -> None:
        if await self.clients.get(client_id) is None:
            raise NotFoundError("client", client_id)

    async def create(
        self,
        *,
        external_id: str,
        device_model_id: int,
        owner_id: int,
        tenant_id: int | None,
        serial_number: str | None,
        ip_address: str | None,
        mac_address: str | None,
        status: str,
        hosting_started_at: dt.date | None,
        comment: str | None,
    ) -> Device:
        if await self.repo.get_model(device_model_id) is None:
            raise NotFoundError("device model", device_model_id)
        tenant_id = tenant_id if tenant_id is not None else owner_id
        await self._require_client(owner_id)
        if tenant_id != owner_id:
            await self._require_client(tenant_id)
        clash = await self.repo.exists_with(external_id, serial_number)
        if clash:
            raise ConflictError(f"a device with this {clash} already exists")

        device = Device(
            external_id=external_id,
            serial_number=serial_number,
            device_model_id=device_model_id,
            owner_id=owner_id,
            tenant_id=tenant_id,
            ip_address=ip_address,
            mac_address=mac_address,
            status=status,
            hosting_started_at=hosting_started_at,
            comment=comment,
        )
        try:
            await self.repo.add(device)
            first_day = hosting_started_at or self.today()
            await self.repo.add_assignment(device.id, tenant_id, first_day)
            await self.session.commit()
        except IntegrityError as exc:  # lost a race against a concurrent create
            await self.session.rollback()
            raise ConflictError("a device with this external_id or serial_number exists") from exc
        return device

    async def list(self, **filters) -> Sequence[Device]:
        return await self.repo.list(**filters)

    async def update(self, device_id: int, changes: dict) -> Device:
        device = await self.get(device_id)
        serial = changes.get("serial_number")
        if serial is not None and serial != device.serial_number:
            if await self.repo.exists_with(None, serial):
                raise ConflictError("a device with this serial_number already exists")
        for name, value in changes.items():
            if name == "status" and value is None:
                continue  # NOT NULL column: an explicit null means "leave unchanged"
            setattr(device, name, value)
        await self.session.commit()
        return device

    async def delete(self, device_id: int) -> None:
        await self.get(device_id)
        await self.repo.delete(device_id)
        await self.session.commit()

    async def assignments(self, device_id: int):
        await self.get(device_id)
        return await self.repo.assignments(device_id)

    async def transfer(
        self, device_id: int, *, to_tenant_id: int, effective_on: dt.date | None
    ) -> Device:
        """Rent the device to another client from ``effective_on`` (default: today).

        The assignment history is append-only: a transfer cannot be dated on or before the
        latest existing assignment, because that would silently re-bill days that may already
        have been invoiced. Future-dated transfers are allowed; ``tenant_id`` on the device
        always mirrors the latest assignment.
        """
        device = await self.get(device_id)
        await self._require_client(to_tenant_id)
        if device.tenant_id == to_tenant_id:
            raise ConflictError("the device is already rented by this client")

        day = effective_on or self.today()
        history = await self.repo.assignments(device_id)
        if history and day <= history[-1].effective_from:
            raise ConflictError(
                "effective_on must be later than the latest assignment "
                f"({history[-1].effective_from.isoformat()})"
            )
        await self.repo.add_assignment(device_id, to_tenant_id, day)
        device.tenant_id = to_tenant_id
        await self.session.commit()
        return device


# -------------------------------------------------------------------------------- stats


@dataclass(frozen=True)
class StatView:
    device_id: int
    day: dt.date
    effective: EffectiveStat
    original: dict[str, float | None]
    comment: str | None


class StatsService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.devices = DeviceRepository(session)
        self.repo = StatsRepository(session)

    async def _require_device(self, device_id: int) -> None:
        if await self.devices.get(device_id) is None:
            raise NotFoundError("device", device_id)

    async def _view(self, device_id: int, day: dt.date) -> StatView:
        stat = await self.repo.get_stat(device_id, day)
        if stat is None:
            raise NotFoundError("daily stat", f"{device_id}/{day.isoformat()}")
        override = await self.repo.get_override(stat.id)
        original = {name: getattr(stat, name) for name in FIELDS}
        corrections = {name: getattr(override, name) for name in FIELDS} if override else None
        return StatView(
            device_id=device_id,
            day=day,
            effective=apply_override(original, corrections),
            original=original,
            comment=override.comment if override else None,
        )

    async def put_stat(self, device_id: int, day: dt.date, values: dict) -> StatView:
        await self._require_device(device_id)
        try:
            await self.repo.put_stat(device_id, day, values)
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise ConflictError("concurrent update of the same daily stat") from exc
        return await self._view(device_id, day)

    async def get_effective(self, device_id: int, day: dt.date) -> StatView:
        await self._require_device(device_id)
        return await self._view(device_id, day)

    async def put_override(
        self, device_id: int, day: dt.date, values: dict, comment: str | None
    ) -> StatView:
        await self._require_device(device_id)
        stat = await self.repo.get_stat(device_id, day)
        if stat is None:
            raise NotFoundError("daily stat", f"{device_id}/{day.isoformat()}")
        await self.repo.put_override(stat.id, values, comment)
        await self.session.commit()
        return await self._view(device_id, day)

    async def delete_override(self, device_id: int, day: dt.date) -> None:
        await self._require_device(device_id)
        stat = await self.repo.get_stat(device_id, day)
        if stat is None or not await self.repo.delete_override(stat.id):
            raise NotFoundError("override", f"{device_id}/{day.isoformat()}")
        await self.session.commit()

    # ---- bulk

    async def _resolve_items(
        self, items: Sequence[dict]
    ) -> tuple[dict[tuple[int, dt.date], dict], list[str]]:
        """Resolve devices, drop unknown ones, and merge duplicate (device, day) items."""
        by_serial, by_external = await self.devices.resolve(
            [i["serial_number"] for i in items if i.get("serial_number")],
            [i["external_id"] for i in items if i.get("external_id")],
        )
        merged: dict[tuple[int, dt.date], dict] = {}
        unknown: list[str] = []
        for item in items:
            device_id = by_serial.get(item.get("serial_number") or "")
            if device_id is None:
                device_id = by_external.get(item.get("external_id") or "")
            if device_id is None:
                label = item.get("serial_number") or item.get("external_id")
                if label not in unknown:
                    unknown.append(label)
                continue
            blank = {**dict.fromkeys(FIELDS), "comment": None}
            slot = merged.setdefault((device_id, item["date"]), blank)
            for name in (*FIELDS, "comment"):
                if item.get(name) is not None:
                    slot[name] = item[name]  # a later item for the same day wins, per field
        return merged, unknown

    async def bulk_upsert_stats(self, items: Sequence[dict]) -> tuple[int, list[str]]:
        merged, unknown = await self._resolve_items(items)
        rows = [
            {"device_id": device_id, "day": day, **{name: vals[name] for name in FIELDS}}
            for (device_id, day), vals in merged.items()
        ]
        for batch in _chunks(rows, BULK_BATCH_SIZE):
            await self.repo.bulk_upsert_stats(list(batch))
        await self.session.commit()
        return len(rows), unknown

    async def bulk_upsert_overrides(self, items: Sequence[dict]) -> tuple[int, list[str]]:
        """Apply corrections in bulk. Days without telemetry get a stat row seeded with the
        same values, so an imported client report is never lost for lack of a base record."""
        merged, unknown = await self._resolve_items(items)
        rows = [
            {"device_id": device_id, "day": day, **{name: vals[name] for name in FIELDS}}
            for (device_id, day), vals in merged.items()
        ]
        comments = {key: vals["comment"] for key, vals in merged.items()}
        for batch in _chunks(rows, BULK_BATCH_SIZE):
            stat_ids = await self.repo.ensure_stats_exist(list(batch))
            override_rows = [
                {
                    "stat_id": stat_ids[(row["device_id"], row["day"])],
                    **{name: row[name] for name in FIELDS},
                    "comment": comments[(row["device_id"], row["day"])],
                }
                for row in batch
            ]
            await self.repo.bulk_upsert_overrides(override_rows)
        await self.session.commit()
        return len(rows), unknown

    # ---- matrix

    async def matrix(
        self, filters: MatrixFilters, *, limit: int, cursor: str | None, include_total: bool
    ) -> tuple[list[dict], str | None, int | None]:
        try:
            rows, next_cursor = await self.repo.matrix_page(filters, limit=limit, cursor=cursor)
        except ValueError as exc:
            raise InvalidRequestError(str(exc)) from exc

        data = []
        for r in rows:
            effective = apply_override(
                {
                    "uptime_hours": r.orig_uptime,
                    "consumption_kwh": r.orig_kwh,
                    "hashrate_ths": r.orig_hashrate,
                },
                {
                    "uptime_hours": r.ov_uptime,
                    "consumption_kwh": r.ov_kwh,
                    "hashrate_ths": r.ov_hashrate,
                }
                if r.override_id is not None
                else None,
            )
            data.append(
                {
                    "stat_id": r.stat_id,
                    "device_id": r.device_id,
                    "day": r.day,
                    "uptime_hours": effective.uptime_hours,
                    "consumption_kwh": effective.consumption_kwh,
                    "hashrate_ths": effective.hashrate_ths,
                    "is_override": effective.is_override,
                    "override_id": r.override_id,
                    "original_uptime_hours": r.orig_uptime if effective.is_override else None,
                    "original_consumption_kwh": r.orig_kwh if effective.is_override else None,
                    "original_hashrate_ths": r.orig_hashrate if effective.is_override else None,
                    "serial_number": r.serial_number,
                    "model_name": f"{r.brand} {r.model}",
                    "ip_address": r.ip_address,
                    "owner_name": r.owner_name,
                    "tenant_name": r.tenant_name,
                }
            )
        total = await self.repo.matrix_count(filters) if include_total else None
        return data, next_cursor, total


# ------------------------------------------------------------------------------ billing


class BillingService:
    def __init__(self, session: AsyncSession):
        self.clients = ClientRepository(session)
        self.stats = StatsRepository(session)

    async def client_report(
        self,
        client_id: int,
        date_from: dt.date,
        date_to: dt.date,
        fixed_fee_per_device: Decimal,
    ) -> tuple[Client, ClientReport]:
        if date_to < date_from:
            raise InvalidRequestError("date_to must not be earlier than date_from")
        if (date_to - date_from).days > MAX_REPORT_DAYS:
            raise InvalidRequestError(f"the period is limited to {MAX_REPORT_DAYS} days")
        client = await self.clients.get(client_id)
        if client is None:
            raise NotFoundError("client", client_id)

        tariff = _points(await self.clients.tariff_changes(client_id))
        devices = await self.stats.devices_rented_by(client_id)
        device_ids = [d.id for d in devices]
        assignments = await self.stats.assignments_for(device_ids)
        consumption = await self.stats.effective_consumption(device_ids, date_from, date_to)

        usages = [
            DeviceUsage(
                device_id=d.id,
                serial_number=d.serial_number,
                model_name=f"{d.brand} {d.model}",
                assignments=[Assignment(a.tenant_id, a.effective_from) for a in assignments[d.id]],
                daily_kwh=consumption.get(d.id, []),
            )
            for d in devices
        ]
        report = build_client_report(
            client.id, client.base_price, tariff, usages, date_from, date_to, fixed_fee_per_device
        )
        return client, report
