"""I-06 trạm RIS, gộp vết ADS-B, vết cũ, đối chiếu Remote ID, UAS ID lạ, giao thông có người lái, mức suy giảm trạm."""
from __future__ import annotations

import datetime as dt
import time
import uuid

from b1.identity.roles import Role
from tests.conftest import corridor_point, make_sample


def iso(t: dt.datetime) -> str:
    return t.astimezone(dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def now():
    return dt.datetime.now(dt.timezone.utc)


def station(make_user, sid):
    return make_user(Role.RIS_STATION, station_id=sid)[1]


def adsb(t, icao="7C1234", lon=106.40, lat=11.17, alt=1500.0, dbm=-70.0, callsign="VNA1"):
    return {"stream": "adsb", "time": {"tSource": iso(t), "tStation": iso(t + dt.timedelta(milliseconds=40))}, "icao24": icao,
            "callsign": callsign, "position": {"lat": lat, "lon": lon}, "altitude": {"value": alt, "reference": "AMSL"},
            "groundSpeed": 70.0, "track": 70.0, "signalDbm": dbm}


def rid(t, uas, lon, lat, dbm=-80.0):
    return {"stream": "rid", "time": {"tSource": iso(t), "tStation": iso(t + dt.timedelta(milliseconds=50))},
            "transport": "bt5_long_range", "uasId": uas, "uasIdType": "serial", "position": {"lat": lat, "lon": lon},
            "altitude": {"value": 120, "reference": "AGL"}, "signalDbm": dbm}


def status(t, backhaul="fiber", power="grid"):
    return {"stream": "status", "time": {"tStation": iso(t)}, "backhaul": backhaul, "power": power, "ptpLocked": True}


def post(client, h, sid, recs, **kw):
    return client.post(f"/ris/v1/stations/{sid}/records", headers=h, json={"records": recs, **kw})


def test_station_identity_and_schema(client, app, make_user):
    h1 = station(make_user, "RIS-1")
    t = now()
    assert post(client, h1, "RIS-2", [status(t)]).status_code == 403
    _, no_claim = make_user(Role.RIS_STATION)
    assert post(client, no_claim, "RIS-1", [status(t)]).status_code == 403
    _, staff = make_user(Role.CORRIDOR_HEAD, station_id="RIS-1")
    assert post(client, staff, "RIS-1", [status(t)]).status_code == 403
    weather = {"stream": "weather", "time": {"tStation": iso(t)}, "kind": "measured", "windSpeedMs": 4.2, "heightAglM": 10}
    spectrum = {"stream": "spectrum", "time": {"tStation": iso(t)}, "bandMhz": [2400, 2483.5], "peakDbm": -60}
    bad = {"stream": "adsb", "time": {"tStation": iso(t)}, "icao24": "xyz"}
    r = post(client, h1, "RIS-1", [status(t), weather, spectrum, adsb(t, icao="AAA001"), bad])
    assert r.status_code == 202 and r.json()["accepted"] == 4 and [x["index"] for x in r.json()["rejected"]] == [4]
    _, aud = make_user(Role.AUDITOR)
    st = {s["station_id"]: s for s in client.get("/v1/surveillance/stations", headers=aud).json()["items"]}
    assert st["RIS-1"]["level"] == "L0" and st["RIS-1"]["weather"]["windSpeedMs"] == 4.2


def test_adsb_fusion_window_best_signal_and_station_list(client, app, make_user):
    t = now()
    icao = f"{uuid.uuid4().int % 0xFFFFFF:06X}"
    for sid, dbm, ms in (("RIS-1", -75, 0), ("RIS-2", -62, 80), ("RIS-3", -90, 150)):
        post(client, station(make_user, sid), sid, [adsb(t + dt.timedelta(milliseconds=ms), icao=icao, dbm=dbm,
                                                         lat=11.17 + ms / 1e6)])
    tr = {x["key"]: x for x in app.state.surveillance.tracks()["adsb"]}[icao]
    assert tr["stations"] == ["RIS-1", "RIS-2", "RIS-3"] and tr["signal_dbm"] == -62 and tr["lat"] == 11.17 + 80 / 1e6
    # bản tin sau cửa sổ 200 ms là lần báo mới: danh sách trạm làm lại
    post(client, station(make_user, "RIS-4"), "RIS-4", [adsb(t + dt.timedelta(seconds=1), icao=icao, dbm=-95, lat=11.18)])
    tr = {x["key"]: x for x in app.state.surveillance.tracks()["adsb"]}[icao]
    assert tr["stations"] == ["RIS-4"] and tr["lat"] == 11.18
    # bản tin cũ đến muộn bị bỏ
    post(client, station(make_user, "RIS-5"), "RIS-5", [adsb(t, icao=icao, dbm=-40, lat=11.0)])
    assert {x["key"]: x for x in app.state.surveillance.tracks()["adsb"]}[icao]["lat"] == 11.18


def test_stale_track_degrades_then_drops(client, app, make_user):
    sv = app.state.surveillance
    icao = f"{uuid.uuid4().int % 0xFFFFFF:06X}"
    post(client, station(make_user, "RIS-1"), "RIS-1", [adsb(now(), icao=icao)])
    sv.adsb[icao].updated_mono = time.monotonic() - 20
    tr = {x["key"]: x for x in sv.tracks()["adsb"]}[icao]
    assert tr["status"] == "degraded" and 1300 < tr["uncertainty_m"] < 1500
    sv.adsb[icao].updated_mono = time.monotonic() - 31
    app.state.monitor.tick()
    assert icao not in {x["key"] for x in sv.tracks()["adsb"]}


def _fly(client, f, km=3.0, offset=0.0):
    t = now() - dt.timedelta(milliseconds=200)
    t = t.replace(microsecond=t.microsecond // 1000 * 1000)
    r = client.post(f"/telemetry/v1/flights/{f['fid']}/samples", headers=f["tel"],
                    json={"samples": [make_sample(f["fid"], f["uas"], t, km=km, offset_m=offset)]})
    assert r.json()["accepted"] == 1
    return t


def test_rid_reconciliation_ratio_and_mismatch_alert(client, app, flying, make_user):
    f = flying()
    t = _fly(client, f)
    lon, lat = corridor_point(3.0)
    far = corridor_point(3.0, 400)
    h1, h2 = station(make_user, "RIS-1"), station(make_user, "RIS-2")
    post(client, h1, "RIS-1", [rid(t, f["uas"], lon, lat)])
    post(client, h2, "RIS-2", [rid(t, f["uas"], lon + 0.0003, lat)])  # khoảng 33 m: vẫn khớp
    post(client, h1, "RIS-1", [rid(t + dt.timedelta(milliseconds=50), f["uas"], *far)])
    [a] = app.state.monitor.alerts(flight_id=f["fid"], kind="RID_MISMATCH")
    assert a["severity"] == "warning" and 390 < a["data"]["distance_m"] < 410
    _, aud = make_user(Role.AUDITOR)
    rec = client.get(f"/v1/surveillance/flights/{f['fid']}/rid-reconciliation", headers=aud).json()
    assert (rec["total"], rec["matched"], rec["mismatched"]) == (3, 2, 1) and rec["match_ratio"] == round(2 / 3, 4)
    assert rec["by_station"] == {"RIS-1": 2, "RIS-2": 1}
    # ba lần khớp liên tiếp thì tự giải toả
    for i in range(3):
        post(client, h1, "RIS-1", [rid(t + dt.timedelta(milliseconds=i), f["uas"], lon, lat)])
    assert app.state.monitor.alerts(flight_id=f["fid"], kind="RID_MISMATCH")[0]["state"] == "resolved"


def test_unknown_rid_inside_corridor(client, app, make_user):
    uas = f"UNK{uuid.uuid4().hex[:8]}"
    lon, lat = corridor_point(20.0, 1000)
    post(client, station(make_user, "RIS-2"), "RIS-2", [rid(now(), uas, lon, lat)])
    [a] = [x for x in app.state.monitor.alerts(kind="RID_UNKNOWN", open_only=True) if x["subject"] == uas]
    assert a["flight_id"] is None and a["data"]["station"] == "RIS-2"
    uas2 = f"OUT{uuid.uuid4().hex[:8]}"
    post(client, station(make_user, "RIS-2"), "RIS-2", [rid(now(), uas2, *corridor_point(20.0, 5000))])
    assert not [x for x in app.state.monitor.alerts(kind="RID_UNKNOWN") if x["subject"] == uas2]


def test_manned_traffic_near_flight(client, app, flying, make_user):
    f = flying()
    _fly(client, f, km=6.0)
    lon, lat = corridor_point(6.0, 800)
    icao = f"{uuid.uuid4().int % 0xFFFFFF:06X}"
    h = station(make_user, "RIS-1")
    post(client, h, "RIS-1", [adsb(now(), icao=icao, lon=lon, lat=lat, alt=300.0)])
    [a] = app.state.monitor.alerts(flight_id=f["fid"], kind="TRAFFIC")
    assert a["severity"] == "critical" and a["subject"] == icao and 700 < a["data"]["horizontal_m"] < 900
    post(client, h, "RIS-1", [adsb(now() + dt.timedelta(seconds=1), icao=icao, lon=lon, lat=lat, alt=1500.0)])
    assert app.state.monitor.alerts(flight_id=f["fid"], kind="TRAFFIC")[0]["state"] == "resolved"


def test_station_degradation_levels(client, app, make_user):
    sv, m = app.state.surveillance, app.state.monitor
    h = station(make_user, "RIS-3")
    post(client, h, "RIS-3", [status(now(), backhaul="cellular")])
    m.tick()
    [a] = [x for x in m.alerts(kind="STATION", open_only=True) if x["subject"] == "RIS-3"]
    assert a["data"]["level"] == "L1" and a["severity"] == "warning"
    sv.station_seen["RIS-3"] = time.monotonic() - 10
    m.tick()
    [a] = [x for x in m.alerts(kind="STATION", open_only=True) if x["subject"] == "RIS-3"]
    assert a["data"]["level"] == "L2" and a["severity"] == "critical"
    assert {s["station_id"]: s for s in sv.stations()}["RIS-3"]["level"] == "L2"
    post(client, h, "RIS-3", [status(now(), power="ups")])
    m.tick()
    [a] = [x for x in m.alerts(kind="STATION", open_only=True) if x["subject"] == "RIS-3"]
    assert a["data"]["level"] == "L4"
    post(client, h, "RIS-3", [status(now())])
    m.tick()
    assert not [x for x in m.alerts(kind="STATION", open_only=True) if x["subject"] == "RIS-3"]


def test_rid_arriving_before_telemetry_waits_for_it(client, app, flying, make_user):
    """Bản tin Remote ID tới trước mẫu telemetry cùng thời điểm: chờ mẫu đó rồi mới đối chiếu, không báo lệch sai."""
    f = flying()
    t = _fly(client, f, km=3.0)
    t_next = t + dt.timedelta(milliseconds=500)
    lon, lat = corridor_point(3.0, 600)  # phương tiện vừa lệch 600 m; Remote ID tới trước telemetry
    post(client, station(make_user, "RIS-1"), "RIS-1", [rid(t_next, f["uas"], lon, lat)])
    assert app.state.monitor.alerts(flight_id=f["fid"], kind="RID_MISMATCH") == []
    client.post(f"/telemetry/v1/flights/{f['fid']}/samples", headers=f["tel"],
                json={"samples": [make_sample(f["fid"], f["uas"], t_next, km=3.0, offset_m=600)]})
    app.state.monitor.tick()
    rec = app.state.surveillance.reconciliation(f["fid"])
    assert (rec["matched"], rec["mismatched"]) == (1, 0)
    assert app.state.monitor.alerts(flight_id=f["fid"], kind="RID_MISMATCH") == []


def test_rid_of_cleared_flight_without_telemetry_is_not_unknown(client, app, flying, make_user):
    f = flying()
    lon, lat = corridor_point(3.0)
    post(client, station(make_user, "RIS-1"), "RIS-1", [rid(now(), f["uas"], lon, lat)])
    assert not [x for x in app.state.monitor.alerts(kind="RID_UNKNOWN") if x["subject"] == f["uas"]]
    app.state.surveillance._drain_pending(force=True)
    assert app.state.surveillance.reconciliation(f["fid"])["no_telemetry"] == 1


def test_rid_goes_to_flight_not_ct_session_of_same_uas(client, app, flying, make_user):
    """Phiên CT dùng cùng UAS ID với chuyến bay thật: Remote ID phải đối chiếu với chuyến bay, không với phiên CT."""
    f = flying()
    t = _fly(client, f)
    ct = uuid.uuid4()
    lon, lat = corridor_point(0.0)
    row = {"uas_id": f["uas"], "lon": lon, "lat": lat, "t_source": now(), "t_receive": now(), "ground_speed": 0.0, "track": 0.0,
           "c2_state": "ok", "armed": False, "mode": "LOITER", "safety_flags": []}
    app.state.monitor.on_sample({"id": ct, "block_ids": [], "state": "ct", "ct_session": True}, row)
    assert app.state.monitor.flight_of_uas(f["uas"]) == f["fid"]
    lon, lat = corridor_point(3.0)
    post(client, station(make_user, "RIS-1"), "RIS-1", [rid(t, f["uas"], lon, lat)])
    _, aud = make_user(Role.AUDITOR)
    assert client.get(f"/v1/surveillance/flights/{f['fid']}/rid-reconciliation", headers=aud).json()["matched"] == 1
