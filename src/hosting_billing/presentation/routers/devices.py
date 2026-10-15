from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response, status

from hosting_billing.application.services import DeviceService
from hosting_billing.presentation.deps import device_service
from hosting_billing.presentation.schemas import (
    AssignmentOut,
    DeviceCreate,
    DeviceModelIn,
    DeviceModelOut,
    DeviceOut,
    DeviceUpdate,
    TransferIn,
)

router = APIRouter(tags=["devices"])


# ----------------------------------------------------------------------- device models


@router.post(
    "/device-models", response_model=DeviceModelOut, status_code=status.HTTP_201_CREATED
)
async def create_model(body: DeviceModelIn, service: DeviceService = Depends(device_service)):
    return await service.create_model(brand=body.brand, model=body.model)


@router.get("/device-models", response_model=list[DeviceModelOut])
async def list_models(service: DeviceService = Depends(device_service)):
    return await service.list_models()


# --------------------------------------------------------------------------- devices


@router.post("/devices", response_model=DeviceOut, status_code=status.HTTP_201_CREATED)
async def create_device(body: DeviceCreate, service: DeviceService = Depends(device_service)):
    return await service.create(**body.model_dump())


@router.get("/devices", response_model=list[DeviceOut])
async def list_devices(
    status_: str | None = Query(default=None, alias="status"),
    owner_id: int | None = None,
    tenant_id: int | None = None,
    search: str | None = Query(default=None, description="Substring of serial or external id"),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    service: DeviceService = Depends(device_service),
):
    return await service.list(
        status=status_,
        owner_id=owner_id,
        tenant_id=tenant_id,
        search=search,
        limit=limit,
        offset=offset,
    )


@router.get("/devices/{device_id}", response_model=DeviceOut)
async def get_device(device_id: int, service: DeviceService = Depends(device_service)):
    return await service.get(device_id)


@router.patch("/devices/{device_id}", response_model=DeviceOut)
async def update_device(
    device_id: int, body: DeviceUpdate, service: DeviceService = Depends(device_service)
):
    return await service.update(device_id, body.model_dump(exclude_unset=True))


@router.delete("/devices/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_device(device_id: int, service: DeviceService = Depends(device_service)):
    await service.delete(device_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/devices/{device_id}/transfer", response_model=DeviceOut)
async def transfer_device(
    device_id: int, body: TransferIn, service: DeviceService = Depends(device_service)
):
    """Move the device to another tenant from ``effective_on``; recorded in the history."""
    return await service.transfer(
        device_id, to_tenant_id=body.to_tenant_id, effective_on=body.effective_on
    )


@router.get("/devices/{device_id}/assignments", response_model=list[AssignmentOut])
async def device_assignments(device_id: int, service: DeviceService = Depends(device_service)):
    return await service.assignments(device_id)
