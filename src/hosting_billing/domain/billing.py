"""Billing rules.

Two facts change over time and both must be looked up *for the day being billed*:

* the client's electricity price (``TariffPoint`` history), and
* who is renting a device (``Assignment`` history, written on every transfer).

The first version of this service used the client's *current* tariff for the whole period and
the *current* tenant for every day, which silently mis-bills any period that contains a price
change or a transfer. Here every day is priced individually and then grouped into segments.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")


def money(value: Decimal | float | int | str) -> Decimal:
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class TariffPoint:
    """From ``effective_from`` (inclusive) electricity costs ``price_per_kwh``."""

    effective_from: date
    price_per_kwh: Decimal


@dataclass(frozen=True)
class Assignment:
    """From ``effective_from`` (inclusive) the device is rented by ``tenant_id``."""

    tenant_id: int
    effective_from: date


def price_on(base_price: Decimal, changes: Iterable[TariffPoint], day: date) -> Decimal:
    """Price per kWh valid on ``day``: the latest change on/before it, else the base price."""
    price = base_price
    latest: date | None = None
    for change in changes:
        if change.effective_from <= day and (latest is None or change.effective_from >= latest):
            latest, price = change.effective_from, change.price_per_kwh
    return price


def tenant_on(assignments: Iterable[Assignment], day: date) -> int | None:
    """Tenant on ``day``.

    Days before the first recorded assignment are attributed to the first tenant (telemetry
    is often imported after the fact), so ``None`` only means "no assignments at all".
    """
    ordered = sorted(assignments, key=lambda a: a.effective_from)
    if not ordered:
        return None
    current = ordered[0]
    for assignment in ordered:
        if assignment.effective_from <= day:
            current = assignment
    return current.tenant_id


@dataclass(frozen=True)
class DeviceUsage:
    """Everything needed to bill one device; built by the application layer."""

    device_id: int
    serial_number: str | None
    model_name: str
    assignments: Sequence[Assignment]
    daily_kwh: Sequence[tuple[date, float]]  # effective values (overrides already applied)


@dataclass(frozen=True)
class DayCharge:
    day: date
    kwh: float
    price_per_kwh: Decimal
    cost: Decimal


@dataclass(frozen=True)
class Segment:
    """Consecutive billed days that share the same price."""

    date_from: date
    date_to: date
    price_per_kwh: Decimal
    kwh: float
    cost: Decimal


@dataclass(frozen=True)
class DeviceBill:
    device_id: int
    serial_number: str | None
    model_name: str
    days: tuple[DayCharge, ...]
    segments: tuple[Segment, ...]
    total_kwh: float
    energy_cost: Decimal
    fixed_fee: Decimal

    @property
    def total(self) -> Decimal:
        return self.energy_cost + self.fixed_fee


@dataclass(frozen=True)
class ClientReport:
    client_id: int
    date_from: date
    date_to: date
    devices: tuple[DeviceBill, ...]
    total_kwh: float = field(default=0.0)
    energy_cost: Decimal = field(default=Decimal("0.00"))
    fixed_fees: Decimal = field(default=Decimal("0.00"))

    @property
    def total_to_pay(self) -> Decimal:
        return self.energy_cost + self.fixed_fees


def _segments(days: Sequence[DayCharge]) -> tuple[Segment, ...]:
    """Group per-day charges into runs of equal price (rounding happens per segment)."""
    result: list[Segment] = []
    run: list[DayCharge] = []

    def flush() -> None:
        if not run:
            return
        kwh = round(sum(d.kwh for d in run), 6)
        price = run[0].price_per_kwh
        cost = money(Decimal(str(kwh)) * price)
        result.append(Segment(run[0].day, run[-1].day, price, kwh, cost))
        run.clear()

    for charge in days:
        if run and charge.price_per_kwh != run[0].price_per_kwh:
            flush()
        run.append(charge)
    flush()
    return tuple(result)


def build_client_report(
    client_id: int,
    base_price: Decimal,
    tariff_changes: Sequence[TariffPoint],
    devices: Iterable[DeviceUsage],
    date_from: date,
    date_to: date,
    fixed_fee_per_device: Decimal | float | int | str = 0,
) -> ClientReport:
    """Bill ``client_id`` for ``[date_from, date_to]`` (both inclusive).

    A day is billed to the client only if the client was the device's tenant on that day.
    A device that never produced a billable day does not appear in the report and is not
    charged the fixed fee.
    """
    if date_to < date_from:
        raise ValueError("date_to must not be earlier than date_from")
    fee = money(fixed_fee_per_device)

    bills: list[DeviceBill] = []
    for device in sorted(devices, key=lambda d: d.device_id):
        charges: list[DayCharge] = []
        for day, kwh in sorted(device.daily_kwh):
            if not date_from <= day <= date_to:
                continue
            if tenant_on(device.assignments, day) != client_id:
                continue
            price = price_on(base_price, tariff_changes, day)
            charges.append(DayCharge(day, kwh, price, money(Decimal(str(kwh)) * price)))
        if not charges:
            continue
        segments = _segments(charges)
        bills.append(
            DeviceBill(
                device_id=device.device_id,
                serial_number=device.serial_number,
                model_name=device.model_name,
                days=tuple(charges),
                segments=segments,
                total_kwh=round(sum(c.kwh for c in charges), 6),
                energy_cost=sum((s.cost for s in segments), Decimal("0.00")),
                fixed_fee=fee,
            )
        )

    return ClientReport(
        client_id=client_id,
        date_from=date_from,
        date_to=date_to,
        devices=tuple(bills),
        total_kwh=round(sum(b.total_kwh for b in bills), 6),
        energy_cost=sum((b.energy_cost for b in bills), Decimal("0.00")),
        fixed_fees=sum((b.fixed_fee for b in bills), Decimal("0.00")),
    )
