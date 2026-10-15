"""Request / response models (Pydantic v2)."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, model_validator

from hosting_billing.domain import ClientReport, DeviceBill
from hosting_billing.domain.stats import FIELDS

# Money is computed with Decimal and sent as a JSON number rounded to cents.
Money = Annotated[Decimal, PlainSerializer(float, return_type=float, when_used="json")]
Price = Annotated[Decimal, Field(gt=0, max_digits=12, decimal_places=4)]
Hours = Annotated[float, Field(ge=0, le=24)]
NonNegative = Annotated[float, Field(ge=0, le=1_000_000)]


class FromAttributes(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ------------------------------------------------------------------------- device models


class DeviceModelIn(BaseModel):
    brand: str = Field(min_length=1, max_length=100)
    model: str = Field(min_length=1, max_length=100)


class DeviceModelOut(FromAttributes):
    id: int
    brand: str
    model: str


# ------------------------------------------------------------------------------ clients


class ClientCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    contact: str | None = Field(default=None, max_length=500)
    price_per_kwh: Price


class ClientUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    contact: str | None = Field(default=None, max_length=500)


class ClientOut(BaseModel):
    id: int
    name: str
    contact: str | None
    base_price: Money
    current_price: Money
    created_at: dt.datetime


class TariffChangeIn(BaseModel):
    price_per_kwh: Price
    effective_from: dt.date | None = Field(
        default=None, description="First day the price applies. Defaults to today."
    )


class TariffChangeOut(FromAttributes):
    id: int
    effective_from: dt.date
    price_per_kwh: Money


class TariffHistoryOut(BaseModel):
    client_id: int
    base_price: Money
    changes: list[TariffChangeOut]


# ------------------------------------------------------------------------------ devices


class DeviceCreate(BaseModel):
    external_id: str = Field(min_length=1, max_length=100)
    device_model_id: int
    owner_id: int
    tenant_id: int | None = Field(default=None, description="Defaults to the owner.")
    serial_number: str | None = Field(default=None, min_length=1, max_length=100)
    ip_address: str | None = Field(default=None, max_length=50)
    mac_address: str | None = Field(default=None, max_length=50)
    status: str = Field(default="active", min_length=1, max_length=50)
    hosting_started_at: dt.date | None = None
    comment: str | None = None


class DeviceUpdate(BaseModel):
    serial_number: str | None = Field(default=None, min_length=1, max_length=100)
    ip_address: str | None = Field(default=None, max_length=50)
    mac_address: str | None = Field(default=None, max_length=50)
    status: str | None = Field(default=None, min_length=1, max_length=50)
    hosting_started_at: dt.date | None = None
    comment: str | None = None


class DeviceOut(FromAttributes):
    id: int
    external_id: str
    serial_number: str | None
    device_model_id: int
    owner_id: int
    tenant_id: int
    ip_address: str | None
    mac_address: str | None
    status: str
    hosting_started_at: dt.date | None
    comment: str | None


class TransferIn(BaseModel):
    to_tenant_id: int
    effective_on: dt.date | None = Field(
        default=None, description="First day the new tenant is billed. Defaults to today."
    )


class AssignmentOut(FromAttributes):
    id: int
    device_id: int
    tenant_id: int
    effective_from: dt.date


# --------------------------------------------------------------------------------- stats


class StatValues(BaseModel):
    uptime_hours: Hours | None = None
    consumption_kwh: NonNegative | None = None
    hashrate_ths: NonNegative | None = None

    def provided(self) -> dict[str, float]:
        """The metrics that were actually supplied (0 counts; None means "not given")."""
        return {name: getattr(self, name) for name in FIELDS if getattr(self, name) is not None}


class StatIn(StatValues):
    """Full replacement of a day's original telemetry (omitted metrics become NULL)."""


class OverrideIn(StatValues):
    comment: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def _at_least_one_value(self) -> OverrideIn:
        if not self.provided():
            raise ValueError(
                "an override needs at least one of uptime_hours, consumption_kwh, hashrate_ths"
            )
        return self


class EffectiveStatOut(BaseModel):
    device_id: int
    day: dt.date
    uptime_hours: float | None
    consumption_kwh: float | None
    hashrate_ths: float | None
    is_override: bool
    overridden_fields: list[str]
    original: StatValues
    comment: str | None = None


class BulkItem(StatValues):
    serial_number: str | None = Field(default=None, min_length=1, max_length=100)
    external_id: str | None = Field(default=None, min_length=1, max_length=100)
    date: dt.date
    comment: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def _identified_and_non_empty(self) -> BulkItem:
        if not (self.serial_number or self.external_id):
            raise ValueError("provide serial_number or external_id")
        if not self.provided():
            raise ValueError("provide at least one metric")
        return self


class BulkIn(BaseModel):
    items: list[BulkItem] = Field(min_length=1, max_length=5000)


class BulkOut(BaseModel):
    upserted: int
    unknown_devices: list[str]


class MatrixRowOut(BaseModel):
    stat_id: int
    device_id: int
    day: dt.date
    uptime_hours: float | None
    consumption_kwh: float | None
    hashrate_ths: float | None
    is_override: bool
    override_id: int | None
    original_uptime_hours: float | None
    original_consumption_kwh: float | None
    original_hashrate_ths: float | None
    serial_number: str | None
    model_name: str
    ip_address: str | None
    owner_name: str
    tenant_name: str


class MatrixOut(BaseModel):
    data: list[MatrixRowOut]
    next_cursor: str | None
    total: int | None = None


# ------------------------------------------------------------------------------- billing


class DayChargeOut(BaseModel):
    day: dt.date
    kwh: float
    price_per_kwh: Money
    cost: Money


class SegmentOut(BaseModel):
    date_from: dt.date
    date_to: dt.date
    price_per_kwh: Money
    kwh: float
    cost: Money


class DeviceBillOut(BaseModel):
    device_id: int
    serial_number: str | None
    model_name: str
    total_kwh: float
    energy_cost: Money
    fixed_fee: Money
    total: Money
    segments: list[SegmentOut]
    days: list[DayChargeOut] | None = None

    @classmethod
    def from_domain(cls, bill: DeviceBill, include_days: bool) -> DeviceBillOut:
        return cls(
            device_id=bill.device_id,
            serial_number=bill.serial_number,
            model_name=bill.model_name,
            total_kwh=bill.total_kwh,
            energy_cost=bill.energy_cost,
            fixed_fee=bill.fixed_fee,
            total=bill.total,
            segments=[
                SegmentOut(
                    date_from=s.date_from,
                    date_to=s.date_to,
                    price_per_kwh=s.price_per_kwh,
                    kwh=s.kwh,
                    cost=s.cost,
                )
                for s in bill.segments
            ],
            days=(
                [
                    DayChargeOut(day=d.day, kwh=d.kwh, price_per_kwh=d.price_per_kwh, cost=d.cost)
                    for d in bill.days
                ]
                if include_days
                else None
            ),
        )


class ReportSummaryOut(BaseModel):
    client_id: int
    client_name: str
    date_from: dt.date
    date_to: dt.date
    devices_billed: int
    total_kwh: float
    energy_cost: Money
    fixed_fees: Money
    total_to_pay: Money


class ClientReportOut(BaseModel):
    summary: ReportSummaryOut
    devices: list[DeviceBillOut]

    @classmethod
    def from_domain(
        cls, report: ClientReport, client_name: str, include_days: bool = False
    ) -> ClientReportOut:
        return cls(
            summary=ReportSummaryOut(
                client_id=report.client_id,
                client_name=client_name,
                date_from=report.date_from,
                date_to=report.date_to,
                devices_billed=len(report.devices),
                total_kwh=report.total_kwh,
                energy_cost=report.energy_cost,
                fixed_fees=report.fixed_fees,
                total_to_pay=report.total_to_pay,
            ),
            devices=[DeviceBillOut.from_domain(b, include_days) for b in report.devices],
        )


class ConsumptionOut(BaseModel):
    client_id: int
    date_from: dt.date
    date_to: dt.date
    total_consumption_kwh: float
    energy_cost: Money
