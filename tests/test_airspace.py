"""F-03 Vùng trời: khối, ràng buộc, rào địa lý, kế hoạch kích hoạt, gói dữ liệu, hiệp đồng, kích hoạt, đóng; AT-03."""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import re
import time

import pytest

from b1.identity.roles import Role

ROOT = pathlib.Path(__file__).resolve().parent.parent
ED269 = json.loads((ROOT / "api/schemas/ed269-uaszone.schema.json").read_text(encoding="utf-8"))
BODY = {**{k: {"ok": True} for k in ("weather", "ris_stations", "network", "conformance_monitoring", "emergency_plan")},
        "confirm": True}


def now():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


@pytest.fixture(scope="module")
def head(make_user):
    return make_user(Role.CORRIDOR_HEAD)[1]


def plan(client, head, block, start, end):
    r = client.post("/v1/airspace/activations", headers=head,
                    json={"block_id": block, "starts_at": start.isoformat(), "ends_at": end.isoformat()})
    assert r.status_code == 201, r.text
    return r.json()


def coordinate(client, head, aid, sha=None):
    if sha is None:
        sha = client.get(f"/v1/airspace/activations/{aid}/package", headers=head).headers["X-Package-SHA256"]
    return client.post(f"/v1/airspace/activations/{aid}/coordination", headers=head, json={
        "result": "approved", "package_sha256": sha, "reference": "CV 12/HL-ĐHB", "counterpart": "Tiếp cận Tân Sơn Nhất"})


def test_blocks_and_constraints_loaded(client, head):
    blocks = client.get("/v1/airspace/blocks", headers=head).json()["items"]
    assert [b["id"] for b in blocks] == ["block-1", "block-2", "block-3", "block-4"]
    b3 = blocks[2]
    assert {e["constraint_id"] for e in b3["envelope"]} == {"ceiling-approach", "ceiling-outside"}  # Km 34 nằm trong khối 3
    assert {e["constraint_id"] for e in blocks[0]["envelope"]} == {"ceiling-approach"}
    assert (b3["floor_m"], b3["floor_ref"], b3["ceiling_m"], b3["ceiling_ref"]) == (20, "AGL", 150, "AGL")
    cons = {f["id"]: f for f in client.get("/v1/airspace/constraints", headers=head).json()["features"]}
    assert cons["geofence-north-block-4"]["properties"]["kind"] == "hard_geofence"
    assert cons["border-strip-10km"]["geometry"] is None and cons["border-strip-10km"]["properties"]["status"] == "awaiting_data"
    # rào cứng nằm đúng trên cạnh Bắc của khối 4
    north = cons["geofence-north-block-4"]["geometry"]["coordinates"]
    assert all(pt in blocks[3]["geometry"]["coordinates"][0] for pt in north)


def test_hard_geofence_always_in_geofence(client, make_user):
    _, cust = make_user(Role.CUSTOMER_OPERATOR)
    g = client.get("/v1/airspace/geofence", headers=cust).json()
    assert g["can_disable"] is False
    assert "geofence-north-block-4" in {f["id"] for f in g["features"]}


def test_package_formats(client, app, head, make_user):
    import jsonschema

    t0 = now() + dt.timedelta(days=2)
    a = plan(client, head, "block-2", t0, t0 + dt.timedelta(hours=2))
    url = f"/v1/airspace/activations/{a['id']}/package"
    text = client.get(url, headers=head)
    sha = text.headers["X-Package-SHA256"]
    assert re.fullmatch(r"[0-9a-f]{64}", sha) and sha in text.text
    assert text.text.count("Đỉnh ") == 4 and "Khối 2" in text.text and "từ 20 m đến 150 m so với mặt đất" in text.text
    assert "giờ Việt Nam" in text.text and "không phải phép bay" in text.text
    gj = client.get(url, headers=head, params={"format": "geojson"}).json()
    ring = gj["features"][0]["geometry"]["coordinates"][0]
    assert ring[0] == ring[-1] and len(ring) == 5
    assert sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(ring, ring[1:])) > 0  # RFC 7946: ngược chiều kim đồng hồ
    assert gj["features"][0]["properties"]["sha256"] == sha
    ed = client.get(url, headers=head, params={"format": "ed269"}).json()
    jsonschema.validate(ed, ED269)
    z = ed["features"][0]
    assert z["geometry"][0]["lowerLimit"] == 20 and z["geometry"][0]["upperVerticalReference"] == "AGL"
    assert z["applicability"][0]["startDateTime"] == t0.isoformat().replace("+00:00", "Z")
    # ba dạng cùng một nội dung
    content = client.get(url, headers=head, params={"format": "json"}).json()
    assert content["polygon"] == ring == z["geometry"][0]["horizontalProjection"]["coordinates"][0]
    # khách hàng không đọc được gói (có thông tin chuyến của khách khác)
    _, cust = make_user(Role.CUSTOMER_OPERATOR)
    assert client.get(url, headers=cust).status_code == 403


def test_lifecycle_and_events(client, app, head, make_user):
    t0 = now() + dt.timedelta(minutes=20)
    seq0 = client.get("/v1/airspace/state", headers=head).json()["last_seq"]
    a = plan(client, head, "block-3", t0, t0 + dt.timedelta(hours=2))
    act = f"/v1/airspace/activations/{a['id']}"
    # chưa hiệp đồng thì không kích hoạt được
    r = client.post(f"{act}/activate", headers=head)
    assert r.status_code == 409 and "hiệp đồng" in r.json()["detail"]
    # hiệp đồng phải gắn đúng gói đã gửi
    assert coordinate(client, head, a["id"], sha="0" * 64).status_code == 409
    r = coordinate(client, head, a["id"])
    assert r.status_code == 200 and r.json()["state"] == "coordinated" and r.json()["coordination_ref"] == "CV 12/HL-ĐHB"
    t_req = time.monotonic()
    r = client.post(f"{act}/activate", headers=head)
    assert r.status_code == 200 and r.json()["state"] == "active"
    st = {b["id"]: b for b in client.get("/v1/airspace/state", headers=head).json()["blocks"]}
    assert st["block-3"]["status"] == "active" and time.monotonic() - t_req < 5
    g = client.get("/v1/airspace/geofence", headers=head).json()
    assert f"keep-in:{a['id']}" in {f["id"] for f in g["features"]}
    r = client.post(f"{act}/close", headers=head)
    assert r.status_code == 200 and r.json()["state"] == "closed"
    kinds = [e["kind"] for e in client.get("/v1/airspace/events", headers=head, params={"after": seq0}).json()["items"]
             if e["payload"].get("id") == a["id"]]
    assert kinds == ["activation.planned", "activation.coordinated", "activation.active", "activation.closed"]
    logged = [x.action for x in app.state.audit.list(target_id=a["id"])]
    for action in ("airspace.activation.plan", "airspace.activation.coordinate", "airspace.activation.activate",
                   "airspace.activation.close", "airspace.package.export"):
        assert action in logged


def test_cannot_activate_too_early_or_overlap(client, head):
    t0 = now() + dt.timedelta(days=3)
    a = plan(client, head, "block-4", t0, t0 + dt.timedelta(hours=2))
    r = client.post("/v1/airspace/activations", headers=head, json={
        "block_id": "block-4", "starts_at": (t0 + dt.timedelta(hours=1)).isoformat(),
        "ends_at": (t0 + dt.timedelta(hours=3)).isoformat()})
    assert r.status_code == 409
    assert coordinate(client, head, a["id"]).status_code == 200
    r = client.post(f"/v1/airspace/activations/{a['id']}/activate", headers=head)
    assert r.status_code == 409 and "chưa đến giờ" in r.json()["detail"]
    assert client.post(f"/v1/airspace/activations/{a['id']}/cancel", headers=head).json()["state"] == "cancelled"


def test_cannot_close_block_with_cleared_flight(client, app, head, make_user):
    t0 = now() - dt.timedelta(minutes=30)
    a = plan(client, head, "block-4", t0, t0 + dt.timedelta(hours=3))
    assert coordinate(client, head, a["id"]).status_code == 200
    assert client.post(f"/v1/airspace/activations/{a['id']}/activate", headers=head).status_code == 200
    fid = app.state.operations.create_flight(request_ref="HL-20270101-9001", uas_id="SIM0002", customer_id=None,
                                             starts_at=now() + dt.timedelta(minutes=5), ends_at=now() + dt.timedelta(minutes=35),
                                             block_ids=["block-4"], bvlos=True)
    _, dso = make_user(Role.DUTY_SAFETY_OFFICER)
    assert client.post(f"/v1/flights/{fid}/takeoff-clearance", headers=dso, json=BODY).status_code == 200
    r = client.post(f"/v1/airspace/activations/{a['id']}/close", headers=head)
    assert r.status_code == 409 and str(fid) in r.json()["detail"]


def test_plan_day_from_scheduled_flights(client, app, head, onboard):
    o = onboard()
    day = (now() + dt.timedelta(days=10)).astimezone(dt.timezone(dt.timedelta(hours=7))).date()
    st = dt.datetime.combine(day, dt.time(8, 30), tzinfo=dt.timezone(dt.timedelta(hours=7)))
    uas = client.get(f"/v1/registry/aircraft/{o['aircraft_id']}", headers=o["customer"]).json()["uas_id"]
    import uuid
    app.state.operations.create_flight(request_ref="HL-20270110-0001", uas_id=uas, customer_id=uuid.UUID(o["customer_id"]),
                                       starts_at=st, ends_at=st + dt.timedelta(hours=2), block_ids=["block-1"], bvlos=True)
    r = client.post("/v1/airspace/activations/plan", headers=head, json={"day": day.isoformat()})
    assert r.status_code == 200, r.text
    created = [a for a in r.json()["created"] if a["block_id"] == "block-1"]
    assert len(created) == 1 and created[0]["flights"][0]["operator"] == "Khách thử"
    assert created[0]["flights"][0]["vn_registration_no"].startswith("VN-T-")
    text = client.get(f"/v1/airspace/activations/{created[0]['id']}/package", headers=head).text
    assert "HL-20270110-0001" in text and "Khách thử" in text and "08:30" in text
    # chạy lại không lập trùng
    again = client.post("/v1/airspace/activations/plan", headers=head, json={"day": day.isoformat()}).json()
    assert not [a for a in again["created"] if a["block_id"] == "block-1"] and again["skipped"]


@pytest.mark.parametrize("role", [r for r in Role if r is not Role.CORRIDOR_HEAD])
def test_only_corridor_head_manages_airspace(client, make_user, role):
    _, h = make_user(role)
    t0 = now() + dt.timedelta(days=20)
    r = client.post("/v1/airspace/activations", headers=h, json={
        "block_id": "block-1", "starts_at": t0.isoformat(), "ends_at": (t0 + dt.timedelta(hours=1)).isoformat()})
    assert r.status_code == 403
    assert client.patch("/v1/airspace/blocks/block-1/vertical", headers=h, json={"floor_m": 10, "ceiling_m": 120}).status_code == 403


def test_head_without_mfa_cannot_manage(client, make_user):
    _, h = make_user(Role.CORRIDOR_HEAD, mfa=False)
    r = client.post("/v1/airspace/activations/plan", headers=h, json={"day": "2027-02-01"})
    assert r.status_code == 403 and "đa yếu tố" in r.json()["detail"]
