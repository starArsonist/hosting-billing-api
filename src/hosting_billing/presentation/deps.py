"""FastAPI dependencies: database session, service factories and API-key protection."""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from hosting_billing.application.services import (
    BillingService,
    ClientService,
    DeviceService,
    StatsService,
)


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.sessionmaker() as session:
        yield session


async def require_api_key(
    request: Request, x_api_key: str | None = Header(default=None)
) -> None:
    """No-op unless API_KEY is configured; then every protected route needs ``X-API-Key``."""
    expected: str | None = request.app.state.settings.api_key
    if expected is None:
        return
    if x_api_key is None or not secrets.compare_digest(x_api_key, expected):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or missing API key")


def client_service(session: AsyncSession = Depends(get_session)) -> ClientService:
    return ClientService(session)


def device_service(session: AsyncSession = Depends(get_session)) -> DeviceService:
    return DeviceService(session)


def stats_service(session: AsyncSession = Depends(get_session)) -> StatsService:
    return StatsService(session)


def billing_service(session: AsyncSession = Depends(get_session)) -> BillingService:
    return BillingService(session)
