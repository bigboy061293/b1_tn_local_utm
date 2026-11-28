"""F-05, I-07: cổng telemetry HTTPS (lô, gửi bù, khử trùng), trạng thái chuyến, trục thời gian 10 Hz, MAVLink v2, MQTT."""
from __future__ import annotations

import copy
import datetime as dt
import json
import pathlib
import struct
import uuid

import jsonschema
import pytest
import yaml

from b1.identity.roles import Role
from tests.conftest import make_sample

ROOT = pathlib.Path(__file__).resolve().parent.parent
I07 = yaml.safe_load((ROOT / "api/i07-telemetry.openapi.yaml").read_text(encoding="utf-8"))["components"]["schemas"]


def now():
    t = dt.datetime.now(dt.timezone.utc)
    return t.replace(microsecond=t.microsecond // 1000 * 1000)


def post(client, f, samples, path="samples", headers=None):
    return client.post(f"/telemetry/v1/flights/{f['fid']}/{path}", headers=headers or f["tel"], json={"samples": samples})


def test_batch_accepted_with_three_timestamps(client, app, flying, make_user):
    f = flying()
    t0 = now() - dt.timedelta(seconds=1)
    r = post(client, f, [make_sample(f["fid"], f["uas"], t0 + dt.timedelta(milliseconds=100 * i)) for i in range(10)])
    assert r.status_code == 202, r.text
    ack = r.json()
    jsonschema.validate(ack, dict(I07["BatchAck"], components={"schemas": I07}))
    assert ack["accepted"] == 10 and ack["rejected"] == []
    _, aud = make_user(Role.AUDITOR)
    assert client.get(f"/v1/flights/{f['fid']}", headers=aud).json()["state"] == "airborne"
    items = client.get(f"/v1/flights/{f['fid']}/telemetry", headers=aud).json()["items"]
    assert len(items) == 10
    x = items[0]
    ts, tr, ti = (dt.datetime.fromisoformat(x[k].replace("Z", "+00:00")) for k in ("t_source", "t_receive", "t_ingest"))
    assert ts <= tr <= ti and (ti - tr).total_seconds() < 2
    assert x["alt_agl"] == 120.0 and x["alt_amsl"] == 120.0 + app.state.settings.terrain_elev_m


def test_bad_samples_rejected_individually(client, flying):
    f = flying()
    t = now()
    good = make_sample(f["fid"], f["uas"], t)
    no_ref = copy.deepcopy(make_sample(f["fid"], f["uas"], t + dt.timedelta(milliseconds=100)))
    del no_ref["altitude"]["reference"]
    other_uas = make_sample(f["fid"], "SOMEONE-ELSE", t + dt.timedelta(milliseconds=200))
    other_flight = make_sample(uuid.uuid4(), f["uas"], t + dt.timedelta(milliseconds=300))
    future = make_sample(f["fid"], f["uas"], t + dt.timedelta(seconds=60))
    no_ms = make_sample(f["fid"], f["uas"], t + dt.timedelta(milliseconds=400))
    no_ms["timeSource"] = no_ms["timeSource"][:19] + "Z"
    extra = make_sample(f["fid"], f["uas"], t + dt.timedelta(milliseconds=500), speedKmh=50)
    r = post(client, f, [good, no_ref, other_uas, other_flight, future, no_ms, extra])
    ack = r.json()
    assert ack["accepted"] == 1
    reasons = {x["index"]: x["reason"] for x in ack["rejected"]}
    assert set(reasons) == {1, 2, 3, 4, 5, 6}
    assert "altitude" in reasons[1] and "uasId" in reasons[2] and "flightId" in reasons[3] and "tương lai" in reasons[4]
    assert "timeSource" in reasons[5] and "speedKmh" in reasons[6]
    listed = client.get(f"/telemetry/v1/flights/{f['fid']}/rejected", headers=f["tel"]).json()["items"]
    assert len(listed) == 6


def test_dedup_and_backfill_keep_original_timestamps(client, app, flying):
    f = flying()
    t0 = now() - dt.timedelta(seconds=90)
    batch = [make_sample(f["fid"], f["uas"], t0 + dt.timedelta(milliseconds=200 * i)) for i in range(5)]
    assert post(client, f, batch).json()["accepted"] == 5
    again = post(client, f, batch + batch[:1]).json()
    assert again["accepted"] == 0 and again["duplicates"] == 5
    gap = [make_sample(f["fid"], f["uas"], t0 + dt.timedelta(seconds=1, milliseconds=200 * i)) for i in range(5)]
    r = post(client, f, gap + batch[:2], path="backfill").json()
    assert r["accepted"] == 5 and r["duplicates"] == 2
    rows = app.state.telemetry.samples(f["fid"])
    assert [x.backfill for x in rows] == [False] * 5 + [True] * 5
    assert rows[5].t_source == t0 + dt.timedelta(seconds=1)


def test_backfill_rate_limited_per_flight(client, app, flying, monkeypatch):
    """Gửi bù dồn dập bị chặn 429 kèm Retry-After; mẫu thời gian thực không bị hạn mức này."""
    monkeypatch.setattr(app.state.settings, "backfill_rate_per_s", 100.0)
    monkeypatch.setattr(app.state.settings, "backfill_burst", 300.0)
    f = flying()
    t0 = now() - dt.timedelta(seconds=120)
    def chunk(k):
        return [make_sample(f["fid"], f["uas"], t0 + dt.timedelta(milliseconds=100 * (k * 150 + i))) for i in range(150)]
    assert post(client, f, chunk(0), path="backfill").status_code == 202
    assert post(client, f, chunk(1), path="backfill").status_code == 202
    r = post(client, f, chunk(2), path="backfill")
    assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1
    live = [make_sample(f["fid"], f["uas"], now() - dt.timedelta(milliseconds=100 * (5 - i))) for i in range(5)]
    assert post(client, f, live).json()["accepted"] == 5
    other = flying()  # hạn mức tính riêng từng chuyến
    assert post(client, other, [make_sample(other["fid"], other["uas"], t0)], path="backfill").status_code == 202


def test_authorization(client, app, flying, make_user, onboard):
    f = flying()
    s = [make_sample(f["fid"], f["uas"], now())]
    o = f["o"]
    _, no_scope = make_user(Role.CUSTOMER_OPERATOR, org_id=o["org_id"])
    assert post(client, f, s, headers=no_scope).status_code == 403
    other = onboard()
    _, stranger = make_user(Role.CUSTOMER_OPERATOR, org_id=other["org_id"], scope="telemetry:write")
    assert post(client, f, s, headers=stranger).status_code == 403
    _, staff = make_user(Role.CORRIDOR_HEAD, scope="telemetry:write")
    assert post(client, f, s, headers=staff).status_code == 403
    assert client.post(f"/telemetry/v1/flights/{uuid.uuid4()}/samples", headers=f["tel"], json={"samples": s}).status_code == 404
    # chuyến chưa được cho phép cất cánh
    fid2 = app.state.operations.create_flight(request_ref="HL-X", uas_id=f["uas"], customer_id=uuid.UUID(o["customer_id"]),
                                              starts_at=now(), ends_at=now() + dt.timedelta(hours=1), block_ids=["block-1"], bvlos=True)
    r = client.post(f"/telemetry/v1/flights/{fid2}/samples", headers=f["tel"], json={"samples": [make_sample(fid2, f["uas"], now())]})
    assert r.status_code == 403 and "cho phép cất cánh" in r.json()["detail"]
    assert client.post(f"/telemetry/v1/flights/{f['fid']}/samples", headers=f["tel"], json={"samples": []}).status_code == 400


def test_landed_after_disarmed_10s_then_only_backfill(client, app, flying, make_user):
    f = flying()
    t0 = now() - dt.timedelta(seconds=30)
    assert post(client, f, [make_sample(f["fid"], f["uas"], t0)]).json()["accepted"] == 1
    disarmed = [make_sample(f["fid"], f["uas"], t0 + dt.timedelta(seconds=1 + i), armed=False) for i in range(12)]
    post(client, f, disarmed[:6])
    _, aud = make_user(Role.AUDITOR)
    assert client.get(f"/v1/flights/{f['fid']}", headers=aud).json()["state"] == "airborne"
    post(client, f, disarmed[6:])
    assert client.get(f"/v1/flights/{f['fid']}", headers=aud).json()["state"] == "landed"
    late = [make_sample(f["fid"], f["uas"], t0 + dt.timedelta(seconds=20), armed=False)]
    assert post(client, f, late).status_code == 409
    assert post(client, f, late, path="backfill").status_code == 202


def test_timeline_10hz_marks_interpolated_and_gaps(client, flying, make_user):
    f = flying()
    t0 = (now() - dt.timedelta(seconds=10)).replace(microsecond=0)
    ts = [t0 + dt.timedelta(milliseconds=200 * i) for i in range(6)]          # 5 Hz trong 1 giây
    ts += [t0 + dt.timedelta(seconds=4, milliseconds=100 * i) for i in range(3)]  # rồi trống 3 giây
    post(client, f, [make_sample(f["fid"], f["uas"], t, km=2 + i * 0.01) for i, t in enumerate(ts)])
    _, aud = make_user(Role.AUDITOR)
    tl = client.get(f"/v1/flights/{f['fid']}/timeline", headers=aud).json()
    kinds = [x["kind"] for x in tl["items"]]
    assert tl["hz"] == 10 and len(kinds) == 43
    assert kinds[:11] == ["measured", "interpolated"] * 5 + ["measured"]
    assert set(kinds[11:40]) == {"gap"} and kinds[40:] == ["measured"] * 3
    mid = tl["items"][1]
    assert tl["items"][0]["lat"] < mid["lat"] < tl["items"][2]["lat"] or tl["items"][0]["lat"] > mid["lat"] > tl["items"][2]["lat"]


# ---------- MAVLink v2 ----------

FRAMES = json.loads((ROOT / "tests/data/mavlink_pymavlink_frames.json").read_text())


def test_mavlink_decoder_matches_pymavlink_frames():
    from b1.telemetry import mavlink as mv
    key = bytes(range(32))
    got = {k: mv.decode(bytes.fromhex(h), {42: key})[0][0] for k, h in FRAMES.items()}
    assert got["gpi"].fields() == {"time_boot_ms": 60000, "lat": 111347056, "lon": 1064437417, "alt": 150000,
                                   "relative_alt": 120000, "vx": -1000, "vy": 1200, "vz": -10, "hdg": 31800}
    assert got["hb"].fields()["base_mode"] == 133 and got["ss"].fields()["battery_remaining"] == 76
    assert got["gps"].fields()["satellites_visible"] == 17 and got["bat"].fields()["voltages"][0] == 47900
    assert got["gpi_signed"].signed and got["gpi_signed"].signature_ok
    # bộ mã hoá sinh đúng từng byte như pymavlink, kể cả chữ ký
    assert mv.encode(33, struct.unpack("<IiiiihhhH", got["gpi"].payload.ljust(28, b"\0")), sysid=42, compid=1,
                     seq=got["gpi"].seq).hex() == FRAMES["gpi"]
    s = got["gpi_signed"]
    assert mv.encode(33, tuple(s.fields().values()), sysid=42, compid=1, seq=s.seq, key=key, link_id=3,
                     sign_timestamp=s.sign_timestamp).hex() == FRAMES["gpi_signed"]
    # sai một byte thì hỏng CRC
    bad = bytearray(bytes.fromhex(FRAMES["gpi"]))
    bad[15] ^= 0xFF
    assert mv.decode(bytes(bad)) == ([], 1)
    # sai khoá thì chữ ký không hợp lệ
    assert mv.decode(bytes.fromhex(FRAMES["gpi_signed"]), {42: b"\1" * 32})[0][0].signature_ok is False


def _mav_stream(sysid, t_unix, boot_ms, key=None, sign_ts=None, n=3, armed=True):
    from b1.telemetry import mavlink as mv
    from tests.conftest import corridor_point
    lon, lat = corridor_point(2.0)
    kw = {"sysid": sysid, "key": key}
    out = mv.encode(0, (3, 2, 3, 128 | 4 | 1 if armed else 4 | 1, 4, 3), sign_timestamp=sign_ts, **kw)
    out += mv.encode(2, (int(t_unix * 1e6), boot_ms), sign_timestamp=sign_ts and sign_ts + 1, **kw)
    out += mv.encode(1, (1, 1, 1, 300, 48000, 100, 0, 0, 0, 0, 0, 0, 70), sign_timestamp=sign_ts and sign_ts + 2, **kw)
    out += mv.encode(24, (int(t_unix * 1e6), int(lat * 1e7), int(lon * 1e7), 145000, 70, 100, 1500, 31800, 6, 21),
                     sign_timestamp=sign_ts and sign_ts + 3, **kw)
    for i in range(n):
        out += mv.encode(33, (boot_ms + 100 * i, int(lat * 1e7), int(lon * 1e7), 145000, 120000, -1000, 1100, 0, 31800),
                         sign_timestamp=sign_ts and sign_ts + 4 + i, **kw)
    return out


def test_mavlink_gateway_binds_sysid_and_checks_signature(client, app, flying):
    from b1.telemetry.mavlink import MavlinkGateway
    f = flying()
    assert client.post(f"/telemetry/v1/flights/{f['fid']}/mavlink", headers=f["tel"], json={"sysid": 77}).status_code == 201
    gw = MavlinkGateway(app.state.telemetry, app.state.sessionmaker)
    t = now().timestamp() - 2
    acks = gw.handle(_mav_stream(77, t, 50_000))
    assert sum(a["accepted"] for a in acks) == 3 and all(a["rejected"] == [] for a in acks)
    rows = app.state.telemetry.samples(f["fid"])
    x = rows[0]
    assert x.channel == "mavlink" and x.alt_ref == "AMSL" and x.alt_value == 145.0 and x.alt_agl == 120.0
    assert x.fix_type == "rtk_fixed" and x.satellites == 21 and x.hdop == 0.7 and x.battery_pct == 70 and x.mode == "AUTO"
    assert abs(x.t_source.timestamp() - t) < 0.002 and abs(rows[1].t_source.timestamp() - t - 0.1) < 0.002
    assert x.ground_speed == pytest.approx(14.87, abs=0.01) and x.armed
    # sysid lạ bị bỏ qua
    assert gw.handle(_mav_stream(78, t, 50_000)) == [] and gw.stats["unbound"] > 0
    # bật ký: bản tin không ký bị bỏ, bản ký đúng được nhận, phát lại cùng nhãn thời gian bị bỏ
    key = bytes(range(1, 33))
    client.post(f"/telemetry/v1/flights/{f['fid']}/mavlink", headers=f["tel"], json={"sysid": 77, "signing_key_hex": key.hex()})
    gw.refresh_bindings()
    assert gw.handle(_mav_stream(77, t + 1, 51_000)) == [] and gw.stats["signature_rejected"] >= 7
    signed = _mav_stream(77, t + 1, 51_000, key=key, sign_ts=10_000)
    assert sum(a["accepted"] for a in gw.handle(signed)) == 3
    before = gw.stats["signature_rejected"]
    assert gw.handle(signed) == [] and gw.stats["signature_rejected"] == before + 7
    wrong = _mav_stream(77, t + 2, 52_000, key=b"\2" * 32, sign_ts=20_000)
    assert gw.handle(wrong) == []


def test_mavlink_binding_only_for_own_cleared_flight(client, flying, onboard, make_user):
    f = flying()
    other = onboard()
    _, stranger = make_user(Role.CUSTOMER_OPERATOR, org_id=other["org_id"], scope="telemetry:write")
    assert client.post(f"/telemetry/v1/flights/{f['fid']}/mavlink", headers=stranger, json={"sysid": 9}).status_code == 403
    assert client.post(f"/telemetry/v1/flights/{f['fid']}/mavlink", headers=f["tel"],
                       json={"sysid": 9, "signing_key_hex": "zz"}).status_code == 422


def test_mqtt_bridge_uses_same_checks(app, flying):
    pytest.importorskip("paho.mqtt")
    from b1.telemetry.mqtt import MqttBridge
    f = flying()
    br = MqttBridge(app.state.telemetry, "mqtt://localhost:1")
    t = now()
    one = make_sample(f["fid"], f["uas"], t)
    assert br.handle(f"b1/v1/telemetry/{f['fid']}", json.dumps(one).encode())["accepted"] == 1
    batch = {"samples": [make_sample(f["fid"], f["uas"], t + dt.timedelta(milliseconds=100)),
                         make_sample(f["fid"], "WRONG", t + dt.timedelta(milliseconds=200))]}
    ack = br.handle(f"b1/v1/telemetry/{f['fid']}", json.dumps(batch).encode())
    assert ack["accepted"] == 1 and ack["rejected"][0]["index"] == 1
    assert app.state.telemetry.samples(f["fid"])[0].channel == "mqtt"
