"""SQLAlchemy 2.0 models.

Deliberately relationship-free: queries use explicit joins, so nothing can trigger an
implicit lazy load inside an async session (the classic ``MissingGreenlet`` error), and
cascading deletes are enforced by the database (``ON DELETE CASCADE``).
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from sqlalchemy import (
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


def utcnow() -> dt.datetime:
    """Naive UTC timestamp (the schema stores timestamps without time zone)."""
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


class DeviceModel(Base):
    __tablename__ = "device_models"
    __table_args__ = (UniqueConstraint("brand", "model", name="uq_device_models_brand_model"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    brand: Mapped[str] = mapped_column(String(100))
    model: Mapped[str] = mapped_column(String(100))


class Client(Base):
    """A customer. Can own devices and/or rent them (the tenant is who gets billed)."""

    __tablename__ = "clients"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    contact: Mapped[str | None] = mapped_column(String(500))
    # Price per kWh that applies until the first TariffChange takes effect.
    base_price: Mapped[Decimal] = mapped_column(Numeric(12, 4))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


class TariffChange(Base):
    __tablename__ = "tariff_changes"
    __table_args__ = (
        UniqueConstraint("client_id", "effective_from", name="uq_tariff_changes_client_day"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), index=True
    )
    effective_from: Mapped[dt.date] = mapped_column(Date)
    price_per_kwh: Mapped[Decimal] = mapped_column(Numeric(12, 4))


class Device(Base):
    __tablename__ = "devices"

    id: Mapped[int] = mapped_column(primary_key=True)
    external_id: Mapped[str] = mapped_column(String(100), unique=True)
    serial_number: Mapped[str | None] = mapped_column(String(100), unique=True)
    device_model_id: Mapped[int] = mapped_column(ForeignKey("device_models.id"), index=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("clients.id"), index=True)
    # Denormalised "current tenant"; the authoritative history is TenantAssignment.
    tenant_id: Mapped[int] = mapped_column(ForeignKey("clients.id"), index=True)
    ip_address: Mapped[str | None] = mapped_column(String(50))
    mac_address: Mapped[str | None] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(50), default="active")
    hosting_started_at: Mapped[dt.date | None] = mapped_column(Date)
    comment: Mapped[str | None] = mapped_column(Text)


class TenantAssignment(Base):
    """History of who rented a device from which day. Written on creation and every transfer."""

    __tablename__ = "tenant_assignments"
    __table_args__ = (
        UniqueConstraint("device_id", "effective_from", name="uq_tenant_assignments_device_day"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[int] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), index=True
    )
    tenant_id: Mapped[int] = mapped_column(ForeignKey("clients.id"), index=True)
    effective_from: Mapped[dt.date] = mapped_column(Date)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


class DailyStat(Base):
    """Original telemetry for one device and day. Any field may be missing (NULL)."""

    __tablename__ = "daily_stats"
    __table_args__ = (
        UniqueConstraint("device_id", "day", name="uq_daily_stats_device_day"),
        Index("ix_daily_stats_day", "day"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"))
    day: Mapped[dt.date] = mapped_column(Date)
    uptime_hours: Mapped[float | None] = mapped_column(Float)
    consumption_kwh: Mapped[float | None] = mapped_column(Float)
    hashrate_ths: Mapped[float | None] = mapped_column(Float)


class StatOverride(Base):
    """Manual correction layered over a DailyStat; the original row is never modified."""

    __tablename__ = "stat_overrides"

    id: Mapped[int] = mapped_column(primary_key=True)
    stat_id: Mapped[int] = mapped_column(
        ForeignKey("daily_stats.id", ondelete="CASCADE"), unique=True
    )
    uptime_hours: Mapped[float | None] = mapped_column(Float)
    consumption_kwh: Mapped[float | None] = mapped_column(Float)
    hashrate_ths: Mapped[float | None] = mapped_column(Float)
    comment: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
