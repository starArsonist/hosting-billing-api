"""End-to-end billing scenarios: the HTTP layer, the database and the domain rules together."""

from .helpers import make_client, make_device, make_model, put_stat

MARCH = [f"2026-03-{d:02d}" for d in range(1, 7)]  # days 1..6, 10 kWh each


async def scenario(client):
    """A (4.00/kWh, raises to 6.00 on Mar 4) owns two devices; one moves to B (10.00) on Mar 4."""
    a = await make_client(client, "A", "4.00")
    b = await make_client(client, "B", "10.00")
    model = await make_model(client)
    moved = await make_device(client, model["id"], a["id"], serial="SN-MOVED")
    stayed = await make_device(client, model["id"], a["id"], serial="SN-STAYED")
    for device in (moved, stayed):
        for day in MARCH:
            await put_stat(client, device["id"], day, consumption_kwh=10)

    r = await client.post(f"/clients/{a['id']}/tariffs",
                          json={"price_per_kwh": "6.00", "effective_from": "2026-03-04"})
    assert r.status_code == 201
    r = await client.post(f"/devices/{moved['id']}/transfer",
                          json={"to_tenant_id": b["id"], "effective_on": "2026-03-04"})
    assert r.status_code == 200
    return a, b, moved, stayed


def report_url(client_id, **extra):
    query = {"client_id": client_id, "date_from": "2026-03-01", "date_to": "2026-03-31", **extra}
    return "/billing/client-report", query


async def test_each_day_is_billed_to_the_tenant_and_price_of_that_day(client):
    a, b, moved, stayed = await scenario(client)

    url, params = report_url(a["id"], fixed_fee_per_device=50)
    report = (await client.get(url, params=params)).json()
    by_serial = {d["serial_number"]: d for d in report["devices"]}

    # moved device: A only had it for Mar 1-3 at 4.00 -> 3 * 10 * 4 = 120
    assert by_serial["SN-MOVED"]["total_kwh"] == 30 and by_serial["SN-MOVED"]["energy_cost"] == 120
    # stayed device: Mar 1-3 at 4.00 and Mar 4-6 at 6.00 -> 120 + 180 = 300 in two segments
    assert by_serial["SN-STAYED"]["energy_cost"] == 300
    segments = by_serial["SN-STAYED"]["segments"]
    assert [(s["date_from"], s["date_to"], s["price_per_kwh"]) for s in segments] == [
        ("2026-03-01", "2026-03-03", 4.0),
        ("2026-03-04", "2026-03-06", 6.0),
    ]

    summary = report["summary"]
    assert summary["devices_billed"] == 2 and summary["fixed_fees"] == 100
    assert summary["energy_cost"] == 420 and summary["total_to_pay"] == 520

    # B is billed only for the days after the transfer, with B's own price: 3 * 10 * 10 = 300
    url, params = report_url(b["id"], fixed_fee_per_device=50)
    summary_b = (await client.get(url, params=params)).json()["summary"]
    assert summary_b["total_kwh"] == 30 and summary_b["energy_cost"] == 300
    assert summary_b["fixed_fees"] == 50 and summary_b["total_to_pay"] == 350


async def test_no_energy_is_lost_or_double_billed_across_a_transfer(client):
    a, b, *_ = await scenario(client)
    total = 0.0
    for tenant in (a, b):
        url, params = report_url(tenant["id"])
        total += (await client.get(url, params=params)).json()["summary"]["total_kwh"]
    assert total == 120  # 2 devices * 6 days * 10 kWh


async def test_overrides_change_the_invoice_but_not_the_originals(client):
    a, _, _, stayed = await scenario(client)
    r = await client.put(f"/devices/{stayed['id']}/stats/2026-03-01/override",
                         json={"consumption_kwh": 0, "comment": "meter outage"})
    assert r.status_code == 200

    url, params = report_url(a["id"])
    summary = (await client.get(url, params=params)).json()["summary"]
    assert summary["energy_cost"] == 380  # 420 minus the 10 kWh * 4.00 that was zeroed out
    original = (await client.get(f"/devices/{stayed['id']}/stats/2026-03-01")).json()["original"]
    assert original["consumption_kwh"] == 10


async def test_consumption_endpoint_and_optional_day_detail(client):
    a, *_ = await scenario(client)
    r = await client.get("/billing/consumption", params={
        "client_id": a["id"], "date_from": "2026-03-01", "date_to": "2026-03-31"})
    assert r.json()["total_consumption_kwh"] == 90 and r.json()["energy_cost"] == 420

    url, params = report_url(a["id"], include_days="true")
    first = (await client.get(url, params=params)).json()["devices"][0]
    assert first["serial_number"] == "SN-MOVED"
    assert [d["day"] for d in first["days"]] == ["2026-03-01", "2026-03-02", "2026-03-03"]


async def test_a_client_without_billable_days_gets_an_empty_report(client):
    a = await make_client(client, "Idle")
    url, params = report_url(a["id"], fixed_fee_per_device=100)
    report = (await client.get(url, params=params)).json()
    assert report["devices"] == []
    assert report["summary"]["total_to_pay"] == 0 and report["summary"]["devices_billed"] == 0


async def test_billing_input_validation(client):
    a = await make_client(client)
    url, params = report_url(a["id"], date_from="2026-03-31", date_to="2026-03-01")
    assert (await client.get(url, params=params)).status_code == 422
    url, params = report_url(a["id"], date_from="2024-01-01", date_to="2026-03-01")
    assert (await client.get(url, params=params)).status_code == 422  # longer than the limit
    url, params = report_url(999)
    assert (await client.get(url, params=params)).status_code == 404
    url, params = report_url(a["id"], fixed_fee_per_device=-1)
    assert (await client.get(url, params=params)).status_code == 422
