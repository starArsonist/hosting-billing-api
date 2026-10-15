"""Integration-test fixtures: the real app, driven in-process through httpx.

By default each test gets a fresh in-memory SQLite database. Set TEST_DATABASE_URL to a
``postgresql+asyncpg://`` URL to run the same suite against PostgreSQL (CI does both).
"""

from __future__ import annotations

import os

import httpx
import pytest
import pytest_asyncio

from hosting_billing.config import Settings
from hosting_billing.infrastructure.db.base import Base
from hosting_billing.main import create_app

DATABASE_URL = os.getenv("TEST_DATABASE_URL") or "sqlite+aiosqlite:///:memory:"


async def _client_for(settings: Settings):
    app = create_app(settings)
    async with app.state.engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    async with app.state.engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
    await app.state.engine.dispose()


@pytest_asyncio.fixture
async def client():
    settings = Settings(_env_file=None, database_url=DATABASE_URL, api_key=None)
    async for c in _client_for(settings):
        yield c


@pytest_asyncio.fixture
async def secured_client():
    settings = Settings(_env_file=None, database_url=DATABASE_URL, api_key="s3cret")
    async for c in _client_for(settings):
        yield c
