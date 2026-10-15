"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-10-15
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "device_models",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("brand", sa.String(length=100), nullable=False),
        sa.Column("model", sa.String(length=100), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_device_models"),
        sa.UniqueConstraint("brand", "model", name="uq_device_models_brand_model"),
    )
    op.create_table(
        "clients",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("contact", sa.String(length=500), nullable=True),
        sa.Column("base_price", sa.Numeric(precision=12, scale=4), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_clients"),
    )
    op.create_table(
        "tariff_changes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("client_id", sa.Integer(), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("price_per_kwh", sa.Numeric(precision=12, scale=4), nullable=False),
        sa.ForeignKeyConstraint(
            ["client_id"], ["clients.id"],
            name="fk_tariff_changes_client_id_clients", ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_tariff_changes"),
        sa.UniqueConstraint("client_id", "effective_from", name="uq_tariff_changes_client_day"),
    )
    op.create_index("ix_tariff_changes_client_id", "tariff_changes", ["client_id"])

    op.create_table(
        "devices",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("external_id", sa.String(length=100), nullable=False),
        sa.Column("serial_number", sa.String(length=100), nullable=True),
        sa.Column("device_model_id", sa.Integer(), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("ip_address", sa.String(length=50), nullable=True),
        sa.Column("mac_address", sa.String(length=50), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("hosting_started_at", sa.Date(), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["device_model_id"], ["device_models.id"],
            name="fk_devices_device_model_id_device_models",
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["clients.id"], name="fk_devices_owner_id_clients"),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["clients.id"], name="fk_devices_tenant_id_clients"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_devices"),
        sa.UniqueConstraint("external_id", name="uq_devices_external_id"),
        sa.UniqueConstraint("serial_number", name="uq_devices_serial_number"),
    )
    op.create_index("ix_devices_device_model_id", "devices", ["device_model_id"])
    op.create_index("ix_devices_owner_id", "devices", ["owner_id"])
    op.create_index("ix_devices_tenant_id", "devices", ["tenant_id"])

    op.create_table(
        "tenant_assignments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("device_id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["device_id"], ["devices.id"],
            name="fk_tenant_assignments_device_id_devices", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["clients.id"], name="fk_tenant_assignments_tenant_id_clients"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_tenant_assignments"),
        sa.UniqueConstraint(
            "device_id", "effective_from", name="uq_tenant_assignments_device_day"
        ),
    )
    op.create_index("ix_tenant_assignments_device_id", "tenant_assignments", ["device_id"])
    op.create_index("ix_tenant_assignments_tenant_id", "tenant_assignments", ["tenant_id"])

    op.create_table(
        "daily_stats",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("device_id", sa.Integer(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("uptime_hours", sa.Float(), nullable=True),
        sa.Column("consumption_kwh", sa.Float(), nullable=True),
        sa.Column("hashrate_ths", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(
            ["device_id"], ["devices.id"],
            name="fk_daily_stats_device_id_devices", ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_daily_stats"),
        sa.UniqueConstraint("device_id", "day", name="uq_daily_stats_device_day"),
    )
    op.create_index("ix_daily_stats_day", "daily_stats", ["day"])

    op.create_table(
        "stat_overrides",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("stat_id", sa.Integer(), nullable=False),
        sa.Column("uptime_hours", sa.Float(), nullable=True),
        sa.Column("consumption_kwh", sa.Float(), nullable=True),
        sa.Column("hashrate_ths", sa.Float(), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["stat_id"], ["daily_stats.id"],
            name="fk_stat_overrides_stat_id_daily_stats", ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_stat_overrides"),
        sa.UniqueConstraint("stat_id", name="uq_stat_overrides_stat_id"),
    )


def downgrade() -> None:
    op.drop_table("stat_overrides")
    op.drop_index("ix_daily_stats_day", table_name="daily_stats")
    op.drop_table("daily_stats")
    op.drop_index("ix_tenant_assignments_tenant_id", table_name="tenant_assignments")
    op.drop_index("ix_tenant_assignments_device_id", table_name="tenant_assignments")
    op.drop_table("tenant_assignments")
    op.drop_index("ix_devices_tenant_id", table_name="devices")
    op.drop_index("ix_devices_owner_id", table_name="devices")
    op.drop_index("ix_devices_device_model_id", table_name="devices")
    op.drop_table("devices")
    op.drop_index("ix_tariff_changes_client_id", table_name="tariff_changes")
    op.drop_table("tariff_changes")
    op.drop_table("clients")
    op.drop_table("device_models")
