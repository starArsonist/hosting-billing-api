import json
from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from hosting_billing.domain import Assignment, DeviceUsage, TariffPoint, build_client_report
from hosting_billing.presentation.schemas import (
    BulkIn,
    BulkItem,
    ClientCreate,
    ClientReportOut,
    OverrideIn,
    StatIn,
    TariffChangeIn,
)


def test_override_requires_at_least_one_metric():
    with pytest.raises(ValidationError, match="at least one"):
        OverrideIn(comment="only a comment")
    assert OverrideIn(consumption_kwh=70.5).provided() == {"consumption_kwh": 70.5}


def test_zero_is_a_real_value_not_missing():
    assert OverrideIn(consumption_kwh=0).provided() == {"consumption_kwh": 0}


@pytest.mark.parametrize(
    "payload",
    [{"uptime_hours": 25}, {"uptime_hours": -1}, {"consumption_kwh": -0.1}, {"hashrate_ths": -5}],
)
def test_stat_ranges_are_validated(payload):
    with pytest.raises(ValidationError):
        StatIn(**payload)


def test_bulk_item_needs_an_identifier_and_a_metric():
    with pytest.raises(ValidationError, match="serial_number or external_id"):
        BulkItem(date="2026-03-01", consumption_kwh=1)
    with pytest.raises(ValidationError, match="at least one metric"):
        BulkItem(date="2026-03-01", serial_number="SN-1")
    item = BulkItem(date="2026-03-01", external_id="DEV-1", uptime_hours=24)
    assert item.date == date(2026, 3, 1)


def test_bulk_payload_limits():
    with pytest.raises(ValidationError):
        BulkIn(items=[])
    item = {"date": "2026-03-01", "serial_number": "SN-1", "consumption_kwh": 1}
    assert len(BulkIn(items=[item] * 5000).items) == 5000
    with pytest.raises(ValidationError):
        BulkIn(items=[item] * 5001)


def test_prices_must_be_positive_with_sane_precision():
    assert ClientCreate(name="A", price_per_kwh="4.5").price_per_kwh == Decimal("4.5")
    for bad in ("0", "-1", "1.23456"):
        with pytest.raises(ValidationError):
            ClientCreate(name="A", price_per_kwh=bad)
    with pytest.raises(ValidationError):
        TariffChangeIn(price_per_kwh="abc")


def test_client_report_serialises_money_as_numbers_and_days_on_demand():
    usage = DeviceUsage(
        1, "SN-1", "Vendor X-100", [Assignment(1, date(2026, 1, 1))],
        [(date(2026, 3, 1), 10.0), (date(2026, 3, 2), 10.0)],
    )
    domain_report = build_client_report(
        1, Decimal("4.00"), [TariffPoint(date(2026, 3, 2), Decimal("6.00"))], [usage],
        date(2026, 3, 1), date(2026, 3, 31), Decimal("100"),
    )

    compact = json.loads(ClientReportOut.from_domain(domain_report, "Acme").model_dump_json())
    assert compact["summary"]["total_to_pay"] == 200.0  # 40 + 60 + 100
    assert compact["summary"]["devices_billed"] == 1
    assert [s["price_per_kwh"] for s in compact["devices"][0]["segments"]] == [4.0, 6.0]
    assert compact["devices"][0]["days"] is None

    detailed = ClientReportOut.from_domain(domain_report, "Acme", include_days=True)
    assert len(detailed.devices[0].days) == 2
