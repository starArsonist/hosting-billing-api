"""Fill a database with fictional demo data.

    uv run python scripts/seed.py            # uses DATABASE_URL / .env
    uv run python scripts/seed.py --reset    # drop and recreate all tables first

Everything here is invented: client names, serial numbers and readings.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import random
from decimal import Decimal

from hosting_billing.application.services import (
    ClientService,
    DeviceService,
    StatsService,
)
from hosting_billing.config import get_settings
from hosting_billing.infrastructure.db.base import Base
from hosting_billing.infrastructure.db.session import make_engine, make_sessionmaker

START = dt.date(2026, 3, 1)
DAYS = 14


async def seed(reset: bool) -> None:
    engine = make_engine(get_settings().database_url)
    async with engine.begin() as connection:
        if reset:
            await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)

    rng = random.Random(7)
    sessionmaker = make_sessionmaker(engine)
    async with sessionmaker() as session:
        clients = ClientService(session)
        devices = DeviceService(session)
        stats = StatsService(session)

        acme = await clients.create(
            name="Acme Hosting Co", contact=None, price_per_kwh=Decimal("4.20")
        )
        beta = await clients.create(
            name="Beta Miners", contact="beta@example.com", price_per_kwh=Decimal("5.10")
        )
        gamma = await clients.create(
            name="Gamma Labs", contact=None, price_per_kwh=Decimal("4.80")
        )
        await clients.change_tariff(
            acme.id, price_per_kwh=Decimal("4.90"), effective_from=START + dt.timedelta(days=7)
        )

        model_a = await devices.create_model(brand="Vendor", model="X-100")
        model_b = await devices.create_model(brand="Acme", model="Hasher 7")

        created = []
        for index in range(1, 9):
            device = await devices.create(
                external_id=f"DEV-{index:04d}",
                device_model_id=(model_a if index % 2 else model_b).id,
                owner_id=(acme, beta, gamma)[index % 3].id,
                tenant_id=None,
                serial_number=f"SN-DEMO-{index:04d}",
                ip_address=f"10.20.0.{index}",
                mac_address=None,
                status="active",
                hosting_started_at=START,
                comment=None,
            )
            created.append(device)
            for offset in range(DAYS):
                await stats.put_stat(
                    device.id,
                    START + dt.timedelta(days=offset),
                    {
                        "uptime_hours": round(rng.uniform(21.5, 24.0), 2),
                        "consumption_kwh": round(rng.uniform(62.0, 80.0), 3),
                        "hashrate_ths": round(rng.uniform(95.0, 110.0), 2),
                    },
                )

        # one mid-period transfer and one manual correction, so reports have something to show
        await devices.transfer(
            created[0].id, to_tenant_id=gamma.id, effective_on=START + dt.timedelta(days=5)
        )
        await stats.put_override(
            created[1].id, START + dt.timedelta(days=2), {"consumption_kwh": 71.0}, "meter replaced"
        )

    await engine.dispose()
    stats_total = len(created) * DAYS
    print(f"Seeded 3 clients, 2 models, {len(created)} devices, {stats_total} daily stats.")
    print(
        f"Try: /billing/client-report?client_id={acme.id}"
        "&date_from=2026-03-01&date_to=2026-03-14"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--reset", action="store_true", help="drop all tables first")
    asyncio.run(seed(parser.parse_args().reset))
