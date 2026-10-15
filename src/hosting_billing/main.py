"""Application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from . import __version__
from .application.errors import ConflictError, InvalidRequestError, NotFoundError
from .config import Settings, get_settings
from .infrastructure.db.base import Base
from .infrastructure.db.session import make_engine, make_sessionmaker
from .presentation.deps import require_api_key
from .presentation.routers import billing, clients, devices, stats


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    engine = make_engine(settings.database_url)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if settings.auto_create_tables:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
        yield
        await engine.dispose()

    app = FastAPI(
        title="Hosting Billing API",
        version=__version__,
        description=(
            "Accounting for hosted equipment: devices, daily telemetry with auditable "
            "corrections, and billing that respects tariff and tenant history."
        ),
        lifespan=lifespan,
    )
    # Created eagerly (not in lifespan) so the app is usable under test transports that do
    # not run lifespan events.
    app.state.settings = settings
    app.state.engine = engine
    app.state.sessionmaker = make_sessionmaker(engine)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    protected = [Depends(require_api_key)]
    for router in (clients.router, devices.router, stats.router, billing.router):
        app.include_router(router, dependencies=protected)

    @app.get("/health", tags=["health"])
    async def health(request: Request) -> dict[str, str]:
        async with request.app.state.sessionmaker() as session:
            await session.execute(text("SELECT 1"))
        return {"status": "ok"}

    @app.exception_handler(NotFoundError)
    async def _not_found(_: Request, exc: NotFoundError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=404)

    @app.exception_handler(ConflictError)
    async def _conflict(_: Request, exc: ConflictError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(InvalidRequestError)
    async def _invalid(_: Request, exc: InvalidRequestError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=422)

    return app


def app_factory() -> FastAPI:
    """Entry point for ``uvicorn hosting_billing.main:app_factory --factory``."""
    return create_app()
