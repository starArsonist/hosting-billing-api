from __future__ import annotations

from fastapi import APIRouter, Depends, status

from hosting_billing.application.services import ClientService
from hosting_billing.infrastructure.db.models import Client
from hosting_billing.presentation.deps import client_service
from hosting_billing.presentation.schemas import (
    ClientCreate,
    ClientOut,
    ClientUpdate,
    TariffChangeIn,
    TariffChangeOut,
    TariffHistoryOut,
)

router = APIRouter(prefix="/clients", tags=["clients"])


def _out(client: Client, current_price) -> ClientOut:
    return ClientOut(
        id=client.id,
        name=client.name,
        contact=client.contact,
        base_price=client.base_price,
        current_price=current_price,
        created_at=client.created_at,
    )


@router.post("", response_model=ClientOut, status_code=status.HTTP_201_CREATED)
async def create_client(body: ClientCreate, service: ClientService = Depends(client_service)):
    client = await service.create(
        name=body.name, contact=body.contact, price_per_kwh=body.price_per_kwh
    )
    return _out(client, client.base_price)


@router.get("", response_model=list[ClientOut])
async def list_clients(service: ClientService = Depends(client_service)):
    return [_out(c, price) for c, price in await service.list_with_prices()]


@router.get("/{client_id}", response_model=ClientOut)
async def get_client(client_id: int, service: ClientService = Depends(client_service)):
    client = await service.get(client_id)
    return _out(client, await service.current_price(client))


@router.patch("/{client_id}", response_model=ClientOut)
async def update_client(
    client_id: int, body: ClientUpdate, service: ClientService = Depends(client_service)
):
    client = await service.update(client_id, name=body.name, contact=body.contact)
    return _out(client, await service.current_price(client))


@router.post(
    "/{client_id}/tariffs", response_model=TariffChangeOut, status_code=status.HTTP_201_CREATED
)
async def change_tariff(
    client_id: int, body: TariffChangeIn, service: ClientService = Depends(client_service)
):
    """Schedule a price change. It applies from ``effective_from`` and never rewrites history."""
    return await service.change_tariff(
        client_id, price_per_kwh=body.price_per_kwh, effective_from=body.effective_from
    )


@router.get("/{client_id}/tariffs", response_model=TariffHistoryOut)
async def tariff_history(client_id: int, service: ClientService = Depends(client_service)):
    client, changes = await service.tariff_history(client_id)
    return TariffHistoryOut(client_id=client.id, base_price=client.base_price, changes=changes)
