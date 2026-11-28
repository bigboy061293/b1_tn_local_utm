"""F-06 Giám sát tuân thủ và cảnh báo: lệch tuyến, vượt ranh, vượt trần, đường truyền, pin, cờ an toàn, mất telemetry,
chuyển vùng phủ; khử trùng, nâng cấp, tự giải toả; xác nhận; độ trễ cảnh báo."""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import time

import pytest

from b1.identity.roles import Role
from tests.conftest import corridor_point, make_sample

ROOT = pathlib.Path(__file__).resolve().parent.parent


class Feed:
    """Gửi lần lượt từng mẫu thời gian thực cho một chuyến, giờ nguồn tăng 100 ms mỗi mẫu."""

    def __init__(self, client, f):
        self.client, self.f = client, f
        t = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=30)
        self.t = t.replace(microsecond=t.microsecond // 1000 * 1000)

    def send(self, **kw):
        self.t += dt.timedelta(milliseconds=100)
        r = self.client.post(f"/telemetry/v1/flights/{self.f['fid']}/samples", headers=self.f["tel"],
                             json={"samples": [make_sample(self.f["fid"], self.f["uas"], self.t, **kw)]})
        assert r.status_code == 202 and r.json()["accepted"] == 1, r.text
        return r


def alerts(app, fid, kind=None, open_only=False):
    return app.state.monitor.alerts(flight_id=fid, kind=kind, open_only=open_only)


def events(app, alert_id):
    return [e["kind"] for e in app.state.monitor.events_after(0, 100_000) if e["alert"]["id"] == alert_id]


def test_lateral_deviation_escalates_and_resolves(client, app, flying):
    f = flying()
    feed = Feed(client, f)
    feed.send(offset_m=100)
    assert alerts(app, f["fid"]) == []
    feed.send(offset_m=600)
    feed.send(offset_m=620)  # cùng điều kiện: không sinh cảnh báo mới
    [a] = alerts(app, f["fid"], "LATERAL_DEVIATION")
    assert a["severity"] == "warning" and a["data"]["zone"] == "outside_normal" and 590 < a["data"]["lateral_m"] < 610
    feed.send(offset_m=800)
    [a2] = alerts(app, f["fid"], "LATERAL_DEVIATION")
    assert a2["id"] == a["id"] and a2["severity"] == "critical" and a2["data"]["conformance"] == "nonconforming"
    live = {x["flight_id"]: x for x in app.state.monitor.live_flights()}[str(f["fid"])]
    assert live["conformance"] == "nonconforming"
    feed.send(offset_m=0)
    [a3] = alerts(app, f["fid"], "LATERAL_DEVIATION")
    assert a3["state"] == "resolved" and a3["resolved_by"] == "auto"
    assert events(app, a["id"]) == ["alert.raised", "alert.escalated", "alert.resolved"]


def test_boundary_and_ceiling(client, app, flying):
    f = flying()
    feed = Feed(client, f)
    feed.send(offset_m=3200)
    kinds = {a["kind"]: a for a in alerts(app, f["fid"], open_only=True)}
    assert kinds["BOUNDARY"]["severity"] == "critical" and kinds["LATERAL_DEVIATION"]["severity"] == "critical"
    feed.send(altitude={"value": 160.0, "reference": "AGL"})
    kinds = {a["kind"]: a for a in alerts(app, f["fid"], open_only=True)}
    assert set(kinds) == {"CEILING"} and kinds["CEILING"]["data"]["source"] == "block-1"
    # độ cao theo mực nước biển: 25 m địa hình + 130 m = 155 m AMSL, tức 130 m so với mặt đất: dưới trần
    feed.send(altitude={"value": 155.0, "reference": "AMSL"})
    assert alerts(app, f["fid"], open_only=True) == []
    feed.send(altitude={"value": 300.0, "reference": "AMSL", "agl": 140.0})  # có agl thì dùng agl
    assert alerts(app, f["fid"], open_only=True) == []


def test_c2_battery_and_safety_flags(client, app, flying):
    f = flying()
    feed = Feed(client, f)
    feed.send(c2Link={"state": "degraded", "rssiDbm": -95})
    feed.send(c2Link={"state": "lost"}, energy={"batteryPercent": 22.0, "voltage": 44.0})
    feed.send(c2Link={"state": "lost"}, energy={"batteryPercent": 12.0, "voltage": 43.0}, safetyFlags=["c2_lost", "geofence_breach"])
    got = {(a["kind"], a["subject"]): a for a in alerts(app, f["fid"], open_only=True)}
    assert got[("C2_LINK", None)]["severity"] == "critical" and got[("LOW_BATTERY", None)]["severity"] == "critical"
    assert got[("SAFETY_FLAG", "c2_lost")]["severity"] == "critical" and ("SAFETY_FLAG", "geofence_breach") in got
    feed.send(safetyFlags=["geofence_breach"])
    got = {(a["kind"], a["subject"]) for a in alerts(app, f["fid"], open_only=True)}
    assert got == {("SAFETY_FLAG", "geofence_breach")}
    feed.send(energy={"fuelPercent": 10.0})
    assert {a["kind"] for a in alerts(app, f["fid"], open_only=True)} == {"LOW_BATTERY"}


def test_telemetry_stale_while_airborne(client, app, flying):
    f = flying()
    feed = Feed(client, f)
    feed.send()
    m = app.state.monitor
    m.tick(time.monotonic() + 1.0)
    assert alerts(app, f["fid"], "TELEMETRY_STALE") == []
    m.tick(time.monotonic() + 2.5)
    [a] = alerts(app, f["fid"], "TELEMETRY_STALE", open_only=True)
    assert a["severity"] == "critical" and a["data"]["gap_s"] >= 2.0
    m.tick(time.monotonic() + 3.5)
    assert len(alerts(app, f["fid"], "TELEMETRY_STALE")) == 1
    feed.send()
    assert alerts(app, f["fid"], "TELEMETRY_STALE")[0]["state"] == "resolved"


def test_handover_between_stations(client, app, flying):
    f = flying()
    feed = Feed(client, f)
    feed.send(km=3.0)
    feed.send(km=12.0)
    [h] = alerts(app, f["fid"], "HANDOVER")
    assert h["severity"] == "info" and h["state"] == "resolved" and h["data"] == {"from": "RIS-1", "to": "RIS-2", "km": h["data"]["km"]}


def test_acknowledge_requires_privileged_role_and_is_audited(client, app, flying, make_user):
    f = flying()
    Feed(client, f).send(offset_m=600)
    [a] = alerts(app, f["fid"], "LATERAL_DEVIATION")
    _, aud = make_user(Role.AUDITOR)
    assert client.get("/v1/alerts", headers=aud, params={"open": True, "flight_id": str(f["fid"])}).json()["items"][0]["id"] == a["id"]
    assert client.post(f"/v1/alerts/{a['id']}/acknowledge", headers=aud, json={}).status_code == 403
    _, dso = make_user(Role.DUTY_SAFETY_OFFICER)
    r = client.post(f"/v1/alerts/{a['id']}/acknowledge", headers=dso, json={"note": "đã gọi tổ bay"})
    assert r.status_code == 200 and r.json()["state"] == "acknowledged" and r.json()["acked_by"]
    r = client.post(f"/v1/alerts/{a['id']}/acknowledge", headers=dso, json={"resolve": True})
    assert r.json()["state"] == "resolved"
    logged = [x.action for x in app.state.audit.list(target_id=a["id"])]
    assert logged == ["monitoring.alert.acknowledge", "monitoring.alert.resolve"]
    # điều kiện còn thì mẫu sau mở cảnh báo mới
    Feed(client, f).send(offset_m=600)
    assert len(alerts(app, f["fid"], "LATERAL_DEVIATION")) == 2


def test_alert_raised_within_ingest_and_carries_timestamps(client, app, flying, make_user):
    f = flying()
    t_send = dt.datetime.now(dt.timezone.utc)
    Feed(client, f).send(safetyFlags=["emergency"])
    [a] = alerts(app, f["fid"], "SAFETY_FLAG")
    raised = dt.datetime.fromisoformat(a["raised_at"].replace("Z", "+00:00"))
    received = dt.datetime.fromisoformat(a["t_receive"].replace("Z", "+00:00"))
    assert t_send <= received <= raised and (raised - received).total_seconds() < 0.5
    _, aud = make_user(Role.AUDITOR)
    ev = client.get("/v1/alerts/events", headers=aud, params={"after": 0}).json()
    assert any(e["alert"]["id"] == a["id"] and e["kind"] == "alert.raised" for e in ev["items"])


def test_live_snapshot_and_stream(client, app, flying, make_user):
    f = flying()
    Feed(client, f).send(km=5)
    _, aud = make_user(Role.AUDITOR)
    snap = client.get("/v1/live", headers=aud).json()
    me = [x for x in snap["flights"] if x["flight_id"] == str(f["fid"])][0]
    assert me["state"] == "airborne" and me["station"] == "RIS-1" and abs(me["km"] - 5) < 0.1
    assert {s["station_id"] for s in snap["stations"]} == {"RIS-1", "RIS-2", "RIS-3", "RIS-4", "RIS-5"}
    _, cust = make_user(Role.CUSTOMER_OPERATOR)
    assert client.get("/v1/live", headers=cust).status_code == 403


def test_geometry_against_geodesic():
    """Khoảng cách ngang tới tim tuyến, chiều dài tim tuyến khớp đường trắc địa WGS84 (pyproj.Geod) trong 0,1 m."""
    pyproj = pytest.importorskip("pyproj")
    from b1 import geo
    cl = json.loads((ROOT / "config/corridor/centerline.geojson").read_text())["features"][0]["geometry"]["coordinates"]
    g = pyproj.Geod(ellps="WGS84")
    poly = geo.Polyline(cl)
    assert abs(poly.length_m - sum(g.inv(a[0], a[1], b[0], b[1])[2] for a, b in zip(cl, cl[1:]))) < 1.0
    for km in (1, 10, 20, 33, 45, 54):
        foot = corridor_point(km, 0)
        for off in (-3000, -750, 500, 760, 2900):
            p = corridor_point(km, off)
            assert abs(poly.project(p)[0] - g.inv(p[0], p[1], foot[0], foot[1])[2]) < 0.1, (km, off)
