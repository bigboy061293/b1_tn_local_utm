"""Phiên kiểm tra tuân thủ dữ liệu CT-1…8 (Đặc tả Mục 5.3): cách chấm từng bài và luồng mở phiên, gửi, đánh dấu, chấm."""
from __future__ import annotations

import datetime as dt

from b1.identity.roles import Role
from b1.telemetry.ct import ALL_TESTS, evaluate
from tests.conftest import make_sample

REF = {"lat": 11.1347056, "lon": 106.4437417}


def stream(n=18_000, hz=10.0, t0=1_800_000_000.0, lag=0.08, gaps=(), flags=(), backfill=(), jitter_m=0.0):
    """Mẫu tổng hợp: n mẫu ở hz, bỏ các khoảng `gaps` [(a, b)], gắn cờ theo `flags` [(t, flag)]."""
    out = []
    for i in range(n):
        t = t0 + i / hz
        bf = any(a <= t - t0 < b for a, b in backfill)
        if any(a <= t - t0 < b for a, b in gaps) and not bf:
            continue
        fl = [f for (ft, f) in flags if ft <= t - t0 < ft + 3]
        out.append({"id": i, "t_source": t, "t_receive": t + (30 if bf else lag), "lat": REF["lat"] + jitter_m * ((i % 7) - 3) / 3 / 111_000,
                    "lon": REF["lon"], "backfill": bf, "safety_flags": fl})
    return out


def run(samples, alerts=(), marks=None, params=None, rejected=0):
    p = {"min_duration_s": 1800, "rtk_ref": REF, **(params or {})}
    return evaluate(samples, rejected, list(alerts), marks or {}, p, list(ALL_TESTS))


def test_clean_30_minutes_passes_ct1_to_ct4():
    r = run(stream(n=18_001))
    assert all(r[t]["passed"] for t in ("CT-1", "CT-2", "CT-3", "CT-4"))
    assert r["CT-2"]["measured"]["rate_hz"] == 10.0 and r["CT-2"]["measured"]["pct_gaps_le_300ms"] == 100.0
    assert r["CT-3"]["measured"]["median_offset_ms"] == 80.0
    assert r["CT-5"]["passed"] is None and r["CT-6"]["passed"] is None and r["CT-7"]["passed"] is None and r["CT-8"]["passed"] is None


def test_ct1_ct2_failures():
    assert run(stream(n=18_001), rejected=1)["CT-1"]["passed"] is False
    assert run(stream(n=9_000))["CT-1"]["passed"] is False  # 15 phút
    r = run(stream(n=18_001, gaps=[(100.05, 102.6)]))
    assert r["CT-2"]["passed"] is False and r["CT-2"]["measured"]["max_gap_s"] > 2
    assert run(stream(n=18_001 // 2, hz=4.9))["CT-2"]["passed"] is False
    # 2% khoảng 400 ms: dưới 99% khoảng ≤ 300 ms
    many = [(10 + 8 * k, 10.3 + 8 * k) for k in range(220)]
    assert run(stream(n=18_001, gaps=many))["CT-2"]["passed"] is False


def test_ct3_offset_and_monotonic():
    assert run(stream(n=18_001, lag=0.25))["CT-3"]["passed"] is False
    s = stream(n=18_001)
    s[10]["id"], s[11]["id"] = s[11]["id"], s[10]["id"]  # đến sai thứ tự giờ nguồn
    assert run(s)["CT-3"]["measured"]["monotonic"] is False


def test_ct4_rtk_static():
    s = stream(n=6_001, jitter_m=3.0)
    assert run(s, params={"min_duration_s": 60})["CT-4"]["passed"] is True
    s = stream(n=6_001, jitter_m=7.0)
    assert run(s, params={"min_duration_s": 60})["CT-4"]["passed"] is False
    assert run(stream(n=3_000), params={"min_duration_s": 60})["CT-4"]["passed"] is False  # dưới 10 phút
    assert run(stream(n=100), params={"rtk_ref": None})["CT-4"]["passed"] is None


def test_ct5_flag_latency():
    t0 = 1_800_000_000.0
    s = stream(n=600, flags=[(10, "c2_lost"), (30, "geofence_breach")])
    ok = [{"kind": "SAFETY_FLAG", "subject": "c2_lost", "raised": t0 + 10.4}, {"kind": "SAFETY_FLAG", "subject": "geofence_breach", "raised": t0 + 31.2}]
    r = run(s, ok)
    assert r["CT-5"]["passed"] is True and [x["latency_s"] for x in r["CT-5"]["measured"]["onsets"]] == [0.4, 1.2]
    slow = [ok[0], {**ok[1], "raised": t0 + 32.5}]
    assert run(s, slow)["CT-5"]["passed"] is False
    assert run(stream(n=600, flags=[(10, "c2_lost")]), ok[:1])["CT-5"]["passed"] is False  # thiếu vi phạm rào


def test_ct6_from_ingress_headers():
    tls = {"tls_version": "TLSv1.3", "client_cert_verified": "SUCCESS", "client_cert_subject": "CN=kh-demo"}
    assert run(stream(n=10), params={"transport": tls})["CT-6"]["passed"] is True
    assert run(stream(n=10), params={"transport": {**tls, "tls_version": "TLSv1.2"}})["CT-6"]["passed"] is False


def test_ct7_load_window_and_ct8_disconnect_backfill():
    t0 = 1_800_000_000.0
    marks = {"load_start": t0 + 100, "load_end": t0 + 400, "disconnect_start": t0 + 600, "disconnect_end": t0 + 660}
    s = stream(n=12_000, gaps=[(600, 660)], backfill=[(600, 660)])
    for x in s:  # mẫu đầu tiên sau khi nối lại tới sau 3 giây
        if 660 <= x["t_source"] - t0 < 663:
            x["t_receive"] = t0 + 663.1
    r = run(s, marks=marks, params={"min_duration_s": 600})
    assert r["CT-7"]["passed"] is True
    assert r["CT-8"]["passed"] is True and r["CT-8"]["measured"]["backfilled"] == 600
    assert 3 <= r["CT-8"]["measured"]["reconnect_s"] < 3.2
    assert r["CT-2"]["passed"] is True
    no_bf = stream(n=12_000, gaps=[(600, 660)])
    assert run(no_bf, marks=marks, params={"min_duration_s": 600})["CT-8"]["passed"] is False
    s2 = stream(n=12_000, gaps=[(150, 153)])
    assert run(s2, marks=marks, params={"min_duration_s": 600})["CT-7"]["passed"] is False
    # bộ phát nghẽn: nhãn nguồn vẫn đều 10 Hz nhưng mẫu tới dồn cục, cách nhau 5 giây
    s3 = stream(n=12_000)
    for x in s3:
        if 150 <= x["t_source"] - t0 < 250:
            x["t_receive"] = t0 + 150 + 5 * ((x["t_source"] - t0 - 150) // 5 + 1)
    r3 = run(s3, marks=marks, params={"min_duration_s": 600})["CT-7"]
    assert r3["passed"] is False and r3["measured"]["pct_gaps_le_300ms"] == 100.0
    assert r3["measured"]["arrival"]["max_gap_s"] >= 4.9


def test_session_flow_self_check_and_official(client, app, onboard, make_user):
    o = onboard()
    aid = o["aircraft_id"]
    uas = client.get(f"/v1/registry/aircraft/{aid}", headers=o["customer"]).json()["uas_id"]
    _, tel = make_user(Role.CUSTOMER_OPERATOR, org_id=o["org_id"], scope="telemetry:write")
    assert client.post("/v1/ct-sessions", headers=tel, json={"aircraft_id": aid, "official": True}).status_code == 403
    other = onboard()
    assert client.post("/v1/ct-sessions", headers=tel, json={"aircraft_id": other["aircraft_id"]}).status_code == 403
    r = client.post("/v1/ct-sessions", headers=tel, json={"aircraft_id": aid, "min_duration_s": 60, "rtk_ref": REF})
    assert r.status_code == 201, r.text
    sess = r.json()
    sid = sess["id"]
    assert sess["telemetry_url"] == f"/telemetry/v1/flights/{sid}/samples" and sess["official"] is False
    t0 = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=70)
    t0 = t0.replace(microsecond=0)
    for k in range(7):
        batch = [make_sample(sid, uas, t0 + dt.timedelta(milliseconds=100 * i), km=0.0,
                             safetyFlags=["c2_lost"] if i in range(300, 320) else [])
                 for i in range(k * 100, k * 100 + 100)]
        tls = {"X-B1-TLS-Version": "TLSv1.3", "X-B1-Client-Verify": "SUCCESS"}  # tiêu đề do cổng vào TLS gắn
        rr = client.post(f"/telemetry/v1/flights/{sid}/samples", headers={**tel, **tls}, json={"samples": batch})
        assert rr.json()["accepted"] == 100, rr.text
    # phiên tại khu thử: không cảnh báo vượt ranh, lệch tuyến
    kinds = {a["kind"] for a in app.state.monitor.alerts(flight_id=sid)}
    assert kinds == {"SAFETY_FLAG"}
    _, stranger = make_user(Role.CUSTOMER_OPERATOR, org_id=other["org_id"])
    assert client.get(f"/v1/ct-sessions/{sid}", headers=stranger).status_code == 404
    res = client.post(f"/v1/ct-sessions/{sid}/evaluate", headers=tel).json()
    assert res["state"] == "evaluated"
    rs = res["results"]
    assert rs["CT-1"]["passed"] is True and rs["CT-2"]["passed"] is True and rs["CT-6"]["passed"] is True
    assert rs["CT-5"]["passed"] is False and rs["CT-5"]["measured"]["onsets"][0]["flag"] == "c2_lost"  # thiếu vi phạm rào
    assert client.post(f"/v1/ct-sessions/{sid}/evaluate", headers=tel).status_code == 409
    # phiên chính thức do Kỹ thuật viên mở và chấm, ghi vào hồ sơ
    r = client.post("/v1/ct-sessions", headers=o["tech"], json={"aircraft_id": aid, "official": True, "tests": ["CT-1", "CT-6"]})
    assert r.status_code == 201
    oid = r.json()["id"]
    assert client.post(f"/v1/ct-sessions/{oid}/marks", headers=tel, json={"kind": "load_start"}).status_code == 200
    client.post(f"/telemetry/v1/flights/{oid}/samples", headers=tel, json={"samples": [make_sample(oid, uas, t0, km=0.0)]})
    assert client.post(f"/v1/ct-sessions/{oid}/evaluate", headers=tel).status_code == 403
    ev = client.post(f"/v1/ct-sessions/{oid}/evaluate", headers=o["tech"]).json()
    assert ev["results"]["CT-1"]["passed"] is False and ev["results"]["CT-6"]["passed"] is None
    from sqlalchemy import select

    from b1.registry.models import ConformanceResult
    with app.state.sessionmaker() as s:
        rows = s.scalars(select(ConformanceResult).where(ConformanceResult.report_ref == f"ct_session:{oid}")).all()
    assert [(x.test, x.passed) for x in rows] == [("CT-1", False)]
    with app.state.sessionmaker() as s:
        assert not s.scalars(select(ConformanceResult).where(ConformanceResult.report_ref == f"ct_session:{sid}")).all()
