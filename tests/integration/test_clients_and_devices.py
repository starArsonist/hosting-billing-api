import pytest

from .helpers import make_client, make_device, make_model, put_stat


async def test_health_is_open_and_checks_the_database(client):
    r = await client.get("/health")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


async def test_client_tariff_history_never_rewrites_the_past(client):
    acme = await make_client(client, price="4.00")
    assert acme["base_price"] == 4.0 and acme["current_price"] == 4.0

    tariffs = f"/clients/{acme['id']}/tariffs"
    r = await client.post(tariffs, json={"price_per_kwh": "5.5", "effective_from": "2020-01-01"})
    assert r.status_code == 201 and r.json()["price_per_kwh"] == 5.5
    # a scheduled increase far in the future must not change the current price
    r = await client.post(tariffs, json={"price_per_kwh": "9", "effective_from": "2999-01-01"})
    assert r.status_code == 201

    history = (await client.get(f"/clients/{acme['id']}/tariffs")).json()
    assert history["base_price"] == 4.0
    assert [c["effective_from"] for c in history["changes"]] == ["2020-01-01", "2999-01-01"]

    assert (await client.get(f"/clients/{acme['id']}")).json()["current_price"] == 5.5
    listed = (await client.get("/clients")).json()
    assert listed[0]["current_price"] == 5.5


async def test_duplicate_tariff_date_and_bad_input(client):
    acme = await make_client(client)
    body = {"price_per_kwh": "5", "effective_from": "2026-03-01"}
    assert (await client.post(f"/clients/{acme['id']}/tariffs", json=body)).status_code == 201
    assert (await client.post(f"/clients/{acme['id']}/tariffs", json=body)).status_code == 409
    zero_price = {"name": "x", "price_per_kwh": "0"}
    assert (await client.post("/clients", json=zero_price)).status_code == 422
    assert (await client.get("/clients/999")).status_code == 404
    unknown = await client.post("/clients/999/tariffs", json={"price_per_kwh": "5"})
    assert unknown.status_code == 404


async def test_device_defaults_and_uniqueness(client):
    owner = await make_client(client)
    model = await make_model(client)
    device = await make_device(client, model["id"], owner["id"], serial="SN-1")
    assert device["tenant_id"] == owner["id"]

    assignments = (await client.get(f"/devices/{device['id']}/assignments")).json()
    assert [(a["tenant_id"], a["effective_from"]) for a in assignments] == [
        (owner["id"], "2026-01-01")
    ]

    dup_serial = {"external_id": "OTHER", "device_model_id": model["id"], "owner_id": owner["id"],
                  "serial_number": "SN-1"}
    assert (await client.post("/devices", json=dup_serial)).status_code == 409
    dup_external = {"external_id": device["external_id"], "device_model_id": model["id"],
                    "owner_id": owner["id"]}
    assert (await client.post("/devices", json=dup_external)).status_code == 409
    assert (await client.post("/devices", json={**dup_external, "external_id": "N", "owner_id": 99})
            ).status_code == 404
    assert (await client.post("/devices", json={**dup_external, "external_id": "N",
                                                "device_model_id": 99})).status_code == 404


async def test_device_list_filters_and_literal_search(client):
    a = await make_client(client, "A")
    b = await make_client(client, "B")
    model = await make_model(client)
    await make_device(client, model["id"], a["id"], serial="SN-100")
    await make_device(client, model["id"], b["id"], serial="SN-200", status="offline")

    assert len((await client.get("/devices")).json()) == 2
    assert len((await client.get("/devices", params={"owner_id": b["id"]})).json()) == 1
    assert len((await client.get("/devices", params={"status": "offline"})).json()) == 1
    assert len((await client.get("/devices", params={"search": "sn-1"})).json()) == 1
    # '%' must be treated literally, not as a wildcard
    assert (await client.get("/devices", params={"search": "%"})).json() == []
    assert (await client.get("/devices", params={"limit": 0})).status_code == 422


async def test_update_and_delete_device_cascades_to_stats(client):
    owner = await make_client(client)
    model = await make_model(client)
    d1 = await make_device(client, model["id"], owner["id"], serial="SN-1")
    d2 = await make_device(client, model["id"], owner["id"], serial="SN-2")

    r = await client.patch(f"/devices/{d1['id']}", json={"ip_address": "10.0.0.9"})
    assert r.status_code == 200 and r.json()["ip_address"] == "10.0.0.9"
    r = await client.patch(f"/devices/{d1['id']}", json={"serial_number": "SN-2"})
    assert r.status_code == 409

    await put_stat(client, d2["id"], "2026-03-01", consumption_kwh=10)
    assert (await client.delete(f"/devices/{d2['id']}")).status_code == 204
    assert (await client.get(f"/devices/{d2['id']}")).status_code == 404
    assert (await client.get(f"/devices/{d2['id']}/stats/2026-03-01")).status_code == 404
    assert (await client.delete(f"/devices/{d2['id']}")).status_code == 404


async def test_transfer_appends_history_and_refuses_to_rewrite_it(client):
    a = await make_client(client, "A")
    b = await make_client(client, "B")
    model = await make_model(client)
    device = await make_device(client, model["id"], a["id"])
    url = f"/devices/{device['id']}/transfer"

    r = await client.post(url, json={"to_tenant_id": b["id"], "effective_on": "2026-03-04"})
    assert r.status_code == 200 and r.json()["tenant_id"] == b["id"]
    history = (await client.get(f"/devices/{device['id']}/assignments")).json()
    assert [(h["tenant_id"], h["effective_from"]) for h in history] == [
        (a["id"], "2026-01-01"), (b["id"], "2026-03-04"),
    ]

    same = await client.post(url, json={"to_tenant_id": b["id"], "effective_on": "2026-03-10"})
    assert same.status_code == 409
    backdated = await client.post(url, json={"to_tenant_id": a["id"], "effective_on": "2026-02-01"})
    assert backdated.status_code == 409
    same_day = await client.post(url, json={"to_tenant_id": a["id"], "effective_on": "2026-03-04"})
    assert same_day.status_code == 409
    assert (await client.post(url, json={"to_tenant_id": 999})).status_code == 404


@pytest.mark.parametrize("path", ["/clients", "/devices", "/stats/matrix", "/device-models"])
async def test_api_key_protects_everything_except_health(secured_client, path):
    assert (await secured_client.get("/health")).status_code == 200
    assert (await secured_client.get(path)).status_code == 401
    assert (await secured_client.get(path, headers={"X-API-Key": "wrong"})).status_code == 401
    assert (await secured_client.get(path, headers={"X-API-Key": "s3cret"})).status_code == 200
