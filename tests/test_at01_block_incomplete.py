"""AT-01 Chặn xếp lịch khi thiếu hồ sơ: 100% bị chặn, có nhật ký (Đặc tả B1 v0.1, Mục 9; điều kiện chặn Mục 4.3, F-01).

Mỗi điều kiện chặn được thử riêng ở ba thời điểm: khi nộp yêu cầu, ở kỳ xếp lịch, và ở lần xác nhận cuối T − 3.
Mỗi ca kiểm: không có chuyến bay nào được tạo, có lý do chặn đúng mã quy tắc trong bảng lý do và trong nhật ký truy vết.
"""
from __future__ import annotations

import datetime as dt

import pytest
import sqlalchemy as sa

from b1.audit.service import SYSTEM
from b1.scheduling.calendar import TZ
from b1.scheduling.service import ItemIn

CASES = [  # (thiếu gì khi lập hồ sơ, mã quy tắc mong đợi)
    ({"registration_certificate"}, "REGISTRATION"),
    ({"verify:registration_certificate"}, "REGISTRATION"),
    ({"technical_status_certificate"}, "TECHNICAL_STATUS"),
    ({"pilot_licence"}, "PILOT_LICENCE"),
    ({"device"}, "FLIGHT_MGMT_DEVICE"),
    ({"test_permit"}, "PERMIT"),
    ({"CT-1"}, "CONFORMANCE"),
    ({"CT-8"}, "CONFORMANCE"),
]


@pytest.fixture(autouse=True)
def clean(app):
    with app.state.engine.begin() as c:
        c.execute(sa.text("TRUNCATE scheduling.request_item, scheduling.flight_request, scheduling.block_reason, "
                          "scheduling.scheduling_run, scheduling.param, scheduling.queue_counter, operations.flight"))


def flights(app) -> int:
    with app.state.engine.connect() as c:
        return c.execute(sa.text("SELECT count(*) FROM operations.flight WHERE state <> 'cancelled'")).scalar()


def reasons(app, rid) -> list[tuple[str, str]]:
    with app.state.engine.connect() as c:
        return [tuple(r) for r in c.execute(sa.text(
            "SELECT stage, rule FROM scheduling.block_reason WHERE request_id = :r ORDER BY at"), {"r": rid}).all()]


def blocked_log(app, rid) -> list[dict]:
    return [x.detail for x in app.state.audit.list(action="scheduling.request.blocked", target_id=str(rid))
            if x.outcome == "rejected"]


def body(o, day_offset=10):
    d = dt.datetime.now(TZ).date() + dt.timedelta(days=day_offset)
    return {"aircraft_id": o["aircraft_id"], "pilot_id": o["pilot_id"], "priority_group": 3, "bvlos": True,
            "block_ids": ["block-1"], "items": [{"day": str(d), "slot": 0}]}


@pytest.mark.parametrize("skip,rule", CASES, ids=lambda x: "-".join(sorted(x)) if isinstance(x, set) else x)
def test_blocked_at_submit_and_at_every_run(client, app, onboard, skip, rule):
    o = onboard(skip=skip)
    r = client.post("/v1/scheduling/requests", json=body(o), headers=o["customer"])
    assert r.status_code == 201, r.text
    j = r.json()
    assert j["provisional"] is True and j["status"] == "provisional" and j["queue_no"].startswith("TAM-")
    assert [m["rule"] for m in j["missing"]] == [rule]
    assert reasons(app, j["id"]) == [("submit", rule)]
    assert [m["rule"] for m in blocked_log(app, j["id"])[0]["missing"]] == [rule]

    for _ in range(2):  # mỗi kỳ xếp lịch đều chặn lại và ghi lại
        client.post("/v1/scheduling/runs", headers=o["staff"])
    assert flights(app) == 0
    assert reasons(app, j["id"]) == [("submit", rule), ("run", rule), ("run", rule)]
    assert len(blocked_log(app, j["id"])) == 3
    assert client.get(f"/v1/scheduling/requests/{j['id']}", headers=o["customer"]).json()["status"] == "provisional"


@pytest.mark.parametrize("skip,rule", CASES[:3], ids=lambda x: "-".join(sorted(x)) if isinstance(x, set) else x)
def test_profile_breaks_after_queueing_is_blocked_at_run(client, app, onboard, skip, rule):
    """Hồ sơ đủ lúc nộp, sau đó mất hiệu lực (ví dụ đổi cấu hình làm mất CT): kỳ xếp lịch phải chặn."""
    o = onboard()
    j = client.post("/v1/scheduling/requests", json=body(o), headers=o["customer"]).json()
    assert j["status"] == "queued"
    client.patch(f"/v1/registry/aircraft/{o['aircraft_id']}", json={"config_ref": "fw-2.0/radio-B"}, headers=o["customer"])
    client.post("/v1/scheduling/runs", headers=o["staff"])
    assert flights(app) == 0
    assert reasons(app, j["id"]) == [("run", "CONFORMANCE")]
    assert blocked_log(app, j["id"])[0]["stage"] == "run"


def test_completed_within_hold_keeps_place(client, app, onboard):
    o = onboard(skip={"verify:test_permit"})
    j = client.post("/v1/scheduling/requests", json=body(o), headers=o["customer"]).json()
    assert j["provisional"]
    client.post(f"/v1/registry/documents/{o['docs']['test_permit']}/verify", json={"decision": "verified"}, headers=o["staff"])
    k = client.post(f"/v1/scheduling/requests/{j['id']}/recheck", headers=o["customer"]).json()
    assert k["status"] == "queued" and k["queue_no"].startswith("HL-") and k["queue_at"] == j["queue_at"]
    client.post("/v1/scheduling/runs", headers=o["staff"])
    assert flights(app) == 1


def test_hold_expires_after_three_workdays(app, onboard):
    o = onboard(skip={"pilot_licence"})
    now = dt.datetime(2027, 3, 1, 9, 0, tzinfo=TZ).astimezone(dt.timezone.utc)  # thứ Hai
    svc = app.state.scheduling
    r, missing = svc.submit(SYSTEM, o["customer_id"], aircraft_id=o["aircraft_id"], pilot_id=o["pilot_id"], priority_group=3,
                            bvlos=True, block_ids=["block-1"], items=[ItemIn(dt.date(2027, 3, 20), 0)], now=now)
    assert r.provisional and r.hold_until.astimezone(TZ).date() == dt.date(2027, 3, 4)  # thứ Năm
    svc.run(SYSTEM, now=dt.datetime(2027, 3, 5, 16, 0, tzinfo=TZ))
    r2, _ = svc.recheck(SYSTEM, r.id, now=dt.datetime(2027, 3, 5, 17, 0, tzinfo=TZ))
    assert r2.status == "expired"
    assert flights(app) == 0
    assert app.state.audit.list(action="scheduling.request.expire", target_id=str(r.id))


def test_insurance_checked_at_final_confirmation(app, onboard):
    """Thiếu bảo hiểm: được xếp lịch, nhưng ở lần xác nhận T − 3 ngày làm việc thì khung bị huỷ, chuyến bị huỷ, có nhật ký."""
    o = onboard(skip={"insurance"})
    svc = app.state.scheduling
    mon = dt.datetime(2027, 3, 1, 9, 0, tzinfo=TZ)
    r, missing = svc.submit(SYSTEM, o["customer_id"], aircraft_id=o["aircraft_id"], pilot_id=o["pilot_id"], priority_group=3,
                            bvlos=True, block_ids=["block-1"], items=[ItemIn(dt.date(2027, 3, 15), 0)], now=mon)
    assert not r.provisional and missing == []
    run1 = svc.run(SYSTEM, now=dt.datetime(2027, 3, 1, 16, 0, tzinfo=TZ))  # xếp lịch, chưa tới T − 3
    assert run1.summary.get("scheduled") == 1 and flights(app) == 1
    run2 = svc.run(SYSTEM, now=dt.datetime(2027, 3, 10, 16, 0, tzinfo=TZ))  # thứ Tư = T − 3 ngày làm việc của thứ Hai 15/3
    assert run2.summary.get("cancelled") == 1 and flights(app) == 0
    assert reasons(app, r.id) == [("final", "INSURANCE")]
    assert blocked_log(app, r.id)[0]["stage"] == "final"


def test_at01_summary(client, app, onboard):
    """Tổng hợp AT-01 cho báo cáo: tỷ lệ chặn trên mọi ca thiếu hồ sơ phải là 100%, mọi ca có nhật ký."""
    total = blocked = logged = 0
    for skip, rule in CASES:
        o = onboard(skip=skip)
        j = client.post("/v1/scheduling/requests", json=body(o, day_offset=20), headers=o["customer"]).json()
        client.post("/v1/scheduling/runs", headers=o["staff"])
        total += 1
        blocked += client.get(f"/v1/scheduling/requests/{j['id']}", headers=o["customer"]).json()["items"][0]["flight_id"] is None
        logged += any(rule in [m["rule"] for m in d["missing"]] for d in blocked_log(app, j["id"]))
    assert (total, blocked, logged) == (len(CASES), len(CASES), len(CASES))
    assert flights(app) == 0
