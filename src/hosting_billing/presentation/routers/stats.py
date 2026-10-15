from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, Query, Response, status

from hosting_billing.application.services import StatsService, StatView
from hosting_billing.infrastructure.repositories import MatrixFilters
from hosting_billing.presentation.deps import stats_service
from hosting_billing.presentation.schemas import (
    BulkIn,
    BulkOut,
    EffectiveStatOut,
    MatrixOut,
    OverrideIn,
    StatIn,
    StatValues,
)

router = APIRouter(tags=["stats"])


def _view_out(view: StatView) -> EffectiveStatOut:
    return EffectiveStatOut(
        device_id=view.device_id,
        day=view.day,
        uptime_hours=view.effective.uptime_hours,
        consumption_kwh=view.effective.consumption_kwh,
        hashrate_ths=view.effective.hashrate_ths,
        is_override=view.effective.is_override,
        overridden_fields=list(view.effective.overridden_fields),
        original=StatValues(**view.original),
        comment=view.comment,
    )


@router.put("/devices/{device_id}/stats/{day}", response_model=EffectiveStatOut)
async def put_stat(
    device_id: int,
    day: dt.date,
    body: StatIn,
    service: StatsService = Depends(stats_service),
):
    """Create or replace one day of original telemetry (omitted metrics become empty)."""
    return _view_out(await service.put_stat(device_id, day, body.model_dump()))


@router.get("/devices/{device_id}/stats/{day}", response_model=EffectiveStatOut)
async def get_stat(device_id: int, day: dt.date, service: StatsService = Depends(stats_service)):
    """The effective values (overrides applied) together with the untouched originals."""
    return _view_out(await service.get_effective(device_id, day))


@router.put("/devices/{device_id}/stats/{day}/override", response_model=EffectiveStatOut)
async def put_override(
    device_id: int,
    day: dt.date,
    body: OverrideIn,
    service: StatsService = Depends(stats_service),
):
    """Correct selected metrics of an existing day. Original values are preserved."""
    view = await service.put_override(device_id, day, body.provided(), body.comment)
    return _view_out(view)


@router.delete(
    "/devices/{device_id}/stats/{day}/override", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_override(
    device_id: int, day: dt.date, service: StatsService = Depends(stats_service)
):
    await service.delete_override(device_id, day)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/stats/bulk", response_model=BulkOut)
async def bulk_stats(body: BulkIn, service: StatsService = Depends(stats_service)):
    """Idempotent import keyed by (device, day).

    Devices are matched by ``serial_number`` or ``external_id``; unknown ones are returned in
    ``unknown_devices`` instead of failing the batch. Metrics that are absent from an item do
    not overwrite stored values.
    """
    upserted, unknown = await service.bulk_upsert_stats([i.model_dump() for i in body.items])
    return BulkOut(upserted=upserted, unknown_devices=unknown)


@router.post("/stats/overrides/bulk", response_model=BulkOut)
async def bulk_overrides(body: BulkIn, service: StatsService = Depends(stats_service)):
    """Same as ``/stats/bulk`` but writes corrections (overrides) instead of telemetry."""
    upserted, unknown = await service.bulk_upsert_overrides(
        [i.model_dump() for i in body.items]
    )
    return BulkOut(upserted=upserted, unknown_devices=unknown)


@router.get("/stats/matrix", response_model=MatrixOut)
async def stats_matrix(
    date_from: dt.date | None = None,
    date_to: dt.date | None = None,
    owner_id: int | None = None,
    tenant_id: int | None = None,
    status_: str | None = Query(default=None, alias="status"),
    model: str | None = Query(default=None, description="Substring of the model name"),
    serial: str | None = Query(default=None, description="Substring of the serial number"),
    limit: int = Query(default=100, ge=1, le=1000),
    cursor: str | None = Query(default=None, description="`next_cursor` from the previous page"),
    include_total: bool = False,
    service: StatsService = Depends(stats_service),
):
    """Filterable daily telemetry across all devices, with keyset (cursor) pagination."""
    filters = MatrixFilters(
        date_from=date_from,
        date_to=date_to,
        owner_id=owner_id,
        tenant_id=tenant_id,
        status=status_,
        model=model,
        serial=serial,
    )
    data, next_cursor, total = await service.matrix(
        filters, limit=limit, cursor=cursor, include_total=include_total
    )
    return MatrixOut(data=data, next_cursor=next_cursor, total=total)
