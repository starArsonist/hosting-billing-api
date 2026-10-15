from __future__ import annotations

import datetime as dt
from decimal import Decimal

from fastapi import APIRouter, Depends, Query

from hosting_billing.application.services import BillingService
from hosting_billing.presentation.deps import billing_service
from hosting_billing.presentation.schemas import ClientReportOut, ConsumptionOut

router = APIRouter(prefix="/billing", tags=["billing"])


@router.get("/consumption", response_model=ConsumptionOut)
async def consumption(
    client_id: int,
    date_from: dt.date,
    date_to: dt.date,
    service: BillingService = Depends(billing_service),
):
    """Energy used and its cost for a client, priced day by day with their tariff history."""
    _, report = await service.client_report(client_id, date_from, date_to, Decimal(0))
    return ConsumptionOut(
        client_id=client_id,
        date_from=date_from,
        date_to=date_to,
        total_consumption_kwh=report.total_kwh,
        energy_cost=report.energy_cost,
    )


@router.get("/client-report", response_model=ClientReportOut)
async def client_report(
    client_id: int,
    date_from: dt.date,
    date_to: dt.date,
    fixed_fee_per_device: Decimal = Query(default=Decimal(0), ge=0),
    include_days: bool = False,
    service: BillingService = Depends(billing_service),
):
    """Invoice data for a client.

    * a day is billed to the client only if they rented the device *on that day*;
    * every day uses the price valid *on that day*; consecutive days at one price are
      grouped into ``segments``;
    * overrides replace the original consumption;
    * ``fixed_fee_per_device`` is charged once for each device that has billable days.
    """
    client, report = await service.client_report(
        client_id, date_from, date_to, fixed_fee_per_device
    )
    return ClientReportOut.from_domain(report, client.name, include_days)
