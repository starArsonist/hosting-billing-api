from .helpers import make_client, make_device, make_model, put_stat


async def world(client):
    owner = await make_client(client)
    model = await make_model(client)
    device = await make_device(client, model["id"], owner["id"], serial="SN-1")
    return owner, device


async def test_put_get_and_replace_semantics(client):
    _, device = await world(client)
    day = "2026-03-01"
    first = await put_stat(client, device["id"], day, uptime_hours=20, consumption_kwh=60,
                           hashrate_ths=100)
    assert first["is_override"] is False and first["consumption_kwh"] == 60

    # PUT replaces: omitted metrics become empty rather than keeping stale values
    second = await put_stat(client, device["id"], day, consumption_kwh=61)
    assert second["uptime_hours"] is None and second["consumption_kwh"] == 61
    assert (await client.get(f"/devices/{device['id']}/stats/{day}")).json() == second


async def test_stat_validation_and_unknown_resources(client):
    _, device = await world(client)
    url = f"/devices/{device['id']}/stats/2026-03-01"
    assert (await client.put(url, json={"uptime_hours": 25})).status_code == 422
    assert (await client.put(url, json={"consumption_kwh": -1})).status_code == 422
    assert (await client.put("/devices/999/stats/2026-03-01", json={})).status_code == 404
    assert (await client.get(url)).status_code == 404  # nothing stored yet
    assert (await client.get(f"/devices/{device['id']}/stats/not-a-date")).status_code == 422


async def test_partial_override_keeps_original_and_other_fields(client):
    _, device = await world(client)
    day = "2026-03-01"
    await put_stat(client, device["id"], day, uptime_hours=20, consumption_kwh=60, hashrate_ths=100)

    r = await client.put(
        f"/devices/{device['id']}/stats/{day}/override",
        json={"consumption_kwh": 70.5, "comment": "meter fault"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["consumption_kwh"] == 70.5 and body["uptime_hours"] == 20
    assert body["is_override"] is True and body["overridden_fields"] == ["consumption_kwh"]
    assert body["original"]["consumption_kwh"] == 60 and body["comment"] == "meter fault"

    # zero is a legitimate correction, not "missing"
    override_url = f"/devices/{device['id']}/stats/{day}/override"
    r = await client.put(override_url, json={"consumption_kwh": 0})
    assert r.json()["consumption_kwh"] == 0 and r.json()["comment"] is None

    assert (await client.delete(f"/devices/{device['id']}/stats/{day}/override")).status_code == 204
    restored = (await client.get(f"/devices/{device['id']}/stats/{day}")).json()
    assert restored["consumption_kwh"] == 60 and restored["is_override"] is False
    assert (await client.delete(f"/devices/{device['id']}/stats/{day}/override")).status_code == 404


async def test_override_rules(client):
    _, device = await world(client)
    url = f"/devices/{device['id']}/stats/2026-03-01/override"
    assert (await client.put(url, json={"consumption_kwh": 1})).status_code == 404  # no base stat
    await put_stat(client, device["id"], "2026-03-01", consumption_kwh=5)
    assert (await client.put(url, json={"comment": "nothing to change"})).status_code == 422


async def test_bulk_upsert_matches_by_serial_or_external_id_and_is_idempotent(client):
    _, device = await world(client)
    payload = {"items": [
        {"serial_number": "SN-1", "date": "2026-03-01", "consumption_kwh": 10, "uptime_hours": 20},
        {"external_id": device["external_id"], "date": "2026-03-02", "consumption_kwh": 11},
        {"serial_number": "SN-404", "date": "2026-03-01", "consumption_kwh": 1},
    ]}
    r = await client.post("/stats/bulk", json=payload)
    assert r.status_code == 200
    assert r.json() == {"upserted": 2, "unknown_devices": ["SN-404"]}

    again = await client.post("/stats/bulk", json=payload)
    assert again.json()["upserted"] == 2
    matrix = (await client.get("/stats/matrix", params={"include_total": True})).json()
    assert matrix["total"] == 2  # no duplicates after re-import


async def test_bulk_upsert_never_wipes_existing_metrics(client):
    _, device = await world(client)
    day = "2026-03-01"
    await client.post("/stats/bulk", json={"items": [
        {"serial_number": "SN-1", "date": day, "consumption_kwh": 10, "uptime_hours": 20}]})
    await client.post("/stats/bulk", json={"items": [
        {"serial_number": "SN-1", "date": day, "hashrate_ths": 100}]})

    stat = (await client.get(f"/devices/{device['id']}/stats/{day}")).json()
    assert (stat["uptime_hours"], stat["consumption_kwh"], stat["hashrate_ths"]) == (20, 10, 100)


async def test_bulk_upsert_tolerates_duplicate_items_in_one_payload(client):
    _, device = await world(client)
    items = [
        {"serial_number": "SN-1", "date": "2026-03-01", "consumption_kwh": 10},
        {"serial_number": "SN-1", "date": "2026-03-01", "consumption_kwh": 12, "uptime_hours": 22},
    ]
    r = await client.post("/stats/bulk", json={"items": items})
    assert r.status_code == 200 and r.json()["upserted"] == 1
    stat = (await client.get(f"/devices/{device['id']}/stats/2026-03-01")).json()
    assert stat["consumption_kwh"] == 12 and stat["uptime_hours"] == 22  # later item wins


async def test_bulk_validation(client):
    assert (await client.post("/stats/bulk", json={"items": []})).status_code == 422
    bad = {"items": [{"date": "2026-03-01", "consumption_kwh": 1}]}  # no identifier
    assert (await client.post("/stats/bulk", json=bad)).status_code == 422


async def test_bulk_overrides_create_missing_days_and_keep_originals(client):
    _, device = await world(client)
    await put_stat(client, device["id"], "2026-03-01", consumption_kwh=60, uptime_hours=20)
    r = await client.post("/stats/overrides/bulk", json={"items": [
        {"serial_number": "SN-1", "date": "2026-03-01", "consumption_kwh": 70, "comment": "report"},
        {"serial_number": "SN-1", "date": "2026-03-02", "consumption_kwh": 71, "comment": "report"},
    ]})
    assert r.status_code == 200 and r.json()["upserted"] == 2

    existing = (await client.get(f"/devices/{device['id']}/stats/2026-03-01")).json()
    assert existing["consumption_kwh"] == 70 and existing["original"]["consumption_kwh"] == 60
    assert existing["uptime_hours"] == 20 and existing["comment"] == "report"

    created = (await client.get(f"/devices/{device['id']}/stats/2026-03-02")).json()
    assert created["consumption_kwh"] == 71 and created["is_override"] is True


async def test_matrix_keyset_pagination_and_filters(client):
    owner = await make_client(client, "Owner")
    other = await make_client(client, "Other")
    model = await make_model(client)
    devices = []
    for n, tenant in enumerate([owner, owner, other], start=1):
        d = await make_device(client, model["id"], tenant["id"], serial=f"SN-{n}")
        devices.append(d)
        for day in ("2026-03-01", "2026-03-02", "2026-03-03"):
            await put_stat(client, d["id"], day, uptime_hours=24, consumption_kwh=n)

    seen, cursor, pages = [], None, 0
    while True:
        params = {"limit": 4, **({"cursor": cursor} if cursor else {})}
        page = (await client.get("/stats/matrix", params=params)).json()
        pages += 1
        seen += [(row["device_id"], row["day"]) for row in page["data"]]
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert pages == 3 and len(seen) == 9 and len(set(seen)) == 9  # 4 + 4 + 1, no duplicates
    assert seen == sorted(seen, key=lambda t: (t[0], [-ord(c) for c in t[1]]))  # id asc, day desc

    by_tenant = (await client.get("/stats/matrix", params={"tenant_id": other["id"]})).json()
    assert {row["tenant_name"] for row in by_tenant["data"]} == {"Other"}
    ranged = (await client.get("/stats/matrix", params={"date_from": "2026-03-02",
                                                        "date_to": "2026-03-02"})).json()
    assert len(ranged["data"]) == 3
    assert len((await client.get("/stats/matrix", params={"serial": "sn-2"})).json()["data"]) == 3
    assert len((await client.get("/stats/matrix", params={"model": "x-1"})).json()["data"]) == 9
    assert (await client.get("/stats/matrix", params={"status": "nope"})).json()["data"] == []
    assert (await client.get("/stats/matrix", params={"cursor": "garbage!"})).status_code == 422
    assert (await client.get("/stats/matrix", params={"limit": 0})).status_code == 422


async def test_matrix_shows_overrides_next_to_originals(client):
    _, device = await world(client)
    await put_stat(client, device["id"], "2026-03-01", uptime_hours=20, consumption_kwh=60)
    await client.put(f"/devices/{device['id']}/stats/2026-03-01/override",
                     json={"consumption_kwh": 66})
    row = (await client.get("/stats/matrix")).json()["data"][0]
    assert row["is_override"] is True and row["consumption_kwh"] == 66
    assert row["original_consumption_kwh"] == 60 and row["uptime_hours"] == 20
    assert row["model_name"] == "Vendor X-100" and row["owner_name"] == "Acme"
