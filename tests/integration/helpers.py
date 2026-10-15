"""Small builders so tests read like the scenario they describe."""

from __future__ import annotations


async def make_client(c, name="Acme", price="4.00"):
    r = await c.post("/clients", json={"name": name, "price_per_kwh": price})
    assert r.status_code == 201, r.text
    return r.json()


async def make_model(c, brand="Vendor", model="X-100"):
    r = await c.post("/device-models", json={"brand": brand, "model": model})
    assert r.status_code == 201, r.text
    return r.json()


async def make_device(c, model_id, owner_id, serial="SN-1", **extra):
    payload = {
        "external_id": f"EXT-{serial}",
        "device_model_id": model_id,
        "owner_id": owner_id,
        "serial_number": serial,
        "hosting_started_at": "2026-01-01",
        **extra,
    }
    r = await c.post("/devices", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


async def put_stat(c, device_id, day, **values):
    r = await c.put(f"/devices/{device_id}/stats/{day}", json=values)
    assert r.status_code == 200, r.text
    return r.json()
