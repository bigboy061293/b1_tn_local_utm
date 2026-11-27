"""Xếp lịch (F-02; Đặc tả Mục 4.3, 2.3): số thứ tự, thứ tự xét, giới hạn khối và hành lang, kỳ xếp lịch."""
from __future__ import annotations

import datetime as dt

import pytest
import sqlalchemy as sa

from b1.audit.service import SYSTEM
from b1.scheduling.calendar import TZ
from b1.scheduling.service import ItemIn


@pytest.fixture(autouse=True)
def clean(app):
    with app.state.engine.begin() as c:
        c.execute(sa.text("TRUNCATE scheduling.request_item, scheduling.flight_request, scheduling.block_reason, "
                          "scheduling.scheduling_run, scheduling.param, scheduling.queue_counter, operations.flight"))


def today() -> dt.date:
    return dt.datetime.now(TZ).date()


def submit(client, o, days=(10,), slot=0, blocks=("block-1",), group=3, bvlos=True):
    body = {"aircraft_id": o["aircraft_id"], "pilot_id": o["pilot_id"], "priority_group": group, "bvlos": bvlos,
            "block_ids": list(blocks), "items": [{"day": str(today() + dt.timedelta(days=d)), "slot": slot} for d in days]}
    return client.post("/v1/scheduling/requests", json=body, headers=o["customer"])


def run(client, o):
    r = client.post("/v1/scheduling/runs", headers=o["staff"])
    assert r.status_code == 201, r.text
    return r.json()


def get(client, o, rid):
    return client.get(f"/v1/scheduling/requests/{rid}", headers=o["customer"]).json()


def test_complete_request_gets_queue_number_and_is_scheduled(client, onboard):
    o = onboard()
    r = submit(client, o, days=(10, 11))
    assert r.status_code == 201, r.text
    j = r.json()
    local = dt.datetime.fromisoformat(j["queue_at"]).astimezone(TZ)
    assert j["status"] == "queued" and j["provisional"] is False and j["queue_no"] == f"HL-{local:%Y%m%d}-0001"
    assert j["queue_position"] == 1 and j["missing"] == []
    res = run(client, o)
    assert res["summary"].get("scheduled") == 2
    j = get(client, o, j["id"])
    assert j["status"] == "scheduled" and all(i["status"] == "scheduled" and i["flight_id"] for i in j["items"])
    flight = client.get(f"/v1/flights/{j['items'][0]['flight_id']}", headers=o["staff"]).json()
    assert flight["state"] == "scheduled" and flight["request_ref"] == j["queue_no"]


def test_queue_numbers_increase(client, onboard):
    o = onboard()
    nos = [submit(client, o, days=(10 + i,)).json()["queue_no"] for i in range(3)]
    assert [n[-4:] for n in nos] == ["0001", "0002", "0003"]


@pytest.mark.parametrize("bad,msg", [
    ({"days": (0,)}, "từ ngày mai"),
    ({"days": (61,)}, "từ ngày mai"),
    ({"days": (10, 16)}, "5 ngày liên tục"),
    ({"blocks": ("block-2",)}, "khối đang mở"),
    ({"slot": 4}, "khung trong ngày"),
])
def test_invalid_requests_rejected_and_logged(client, app, onboard, bad, msg):
    o = onboard()
    r = submit(client, o, **bad)
    assert r.status_code == 422 and msg in r.json()["detail"]
    assert app.state.audit.list(action="scheduling.request.submit", target_id=o["customer_id"])[-1].outcome == "rejected"


def test_priority_order_group_then_tier_then_queue(client, onboard):
    """Cùng khối, cùng khung: nhóm ưu tiên trước, rồi hạng khách hàng, rồi số thứ tự."""
    early_c = onboard(tier="C")
    late_a = onboard(tier="A")
    late_c_group2 = onboard(tier="C")
    r1 = submit(client, early_c, days=(12,)).json()
    r2 = submit(client, late_a, days=(12,)).json()
    r3 = submit(client, late_c_group2, days=(12,), group=2).json()
    run(client, early_c)
    st = {x["id"]: get(client, o, x["id"])["items"][0] for x, o in ((r1, early_c), (r2, late_a), (r3, late_c_group2))}
    assert st[r3["id"]]["status"] == "scheduled"                          # nhóm 2 thắng nhóm 3
    assert st[r2["id"]]["status"] == "not_scheduled" and st[r2["id"]]["last_reason"] == "BLOCK_TAKEN"
    assert st[r1["id"]]["status"] == "not_scheduled"


def test_tier_beats_queue_number(client, onboard):
    early_c, late_a = onboard(tier="C"), onboard(tier="A")
    r1 = submit(client, early_c, days=(13,)).json()
    r2 = submit(client, late_a, days=(13,)).json()
    run(client, early_c)
    assert get(client, late_a, r2["id"])["status"] == "scheduled"
    assert get(client, early_c, r1["id"])["items"][0]["last_reason"] == "BLOCK_TAKEN"


def test_corridor_limit_two_concurrent(client, app, onboard):
    o = onboard()
    client.put("/v1/scheduling/params/open_blocks", json={"value": ["block-1", "block-2", "block-3"]}, headers=o["staff"])
    custs = [onboard() for _ in range(3)]
    rs = [submit(client, c, days=(14,), blocks=(b,)).json() for c, b in zip(custs, ("block-1", "block-2", "block-3"))]
    run(client, o)
    got = [get(client, c, r["id"])["items"][0] for c, r in zip(custs, rs)]
    assert [g["status"] for g in got] == ["scheduled", "scheduled", "not_scheduled"]
    assert got[2]["last_reason"] == "CORRIDOR_FULL"


def test_availability_and_public_queue(client, onboard):
    o = onboard()
    rid = submit(client, o, days=(15,)).json()["id"]
    d = str(today() + dt.timedelta(days=15))
    q = client.get("/v1/scheduling/queue", headers=o["customer"]).json()["items"]
    assert q[0]["position"] == 1 and "customer_id" not in q[0]
    run(client, o)
    av = client.get("/v1/scheduling/availability", params={"start": d, "end": d}, headers=o["customer"]).json()["items"]
    assert av[0]["free_blocks"] == [] and av[0]["scheduled_blocks"] == ["block-1"] and av[1]["free_blocks"] == ["block-1"]
    assert get(client, o, rid)["queue_position"] is None


def test_customer_cancel_cancels_flight(client, onboard):
    o = onboard()
    rid = submit(client, o, days=(16,)).json()["id"]
    run(client, o)
    fid = get(client, o, rid)["items"][0]["flight_id"]
    assert client.post(f"/v1/scheduling/requests/{rid}/cancel", headers=o["customer"]).json()["status"] == "cancelled"
    assert client.get(f"/v1/flights/{fid}", headers=o["staff"]).json()["state"] == "cancelled"


def test_other_customer_cannot_see_or_cancel(client, onboard):
    a, b = onboard(), onboard()
    rid = submit(client, a, days=(17,)).json()["id"]
    assert client.get(f"/v1/scheduling/requests/{rid}", headers=b["customer"]).status_code == 403
    assert client.post(f"/v1/scheduling/requests/{rid}/cancel", headers=b["customer"]).status_code == 403
    assert client.get("/v1/scheduling/requests", headers=b["customer"]).json()["items"] == []


def test_only_corridor_head_runs_scheduling(client, onboard, make_user):
    from b1.identity.roles import Role

    o = onboard()
    for role in (Role.DUTY_SAFETY_OFFICER, Role.CUSTOMER_OPERATOR, Role.REGULATOR, Role.IT_ENGINEER):
        assert client.post("/v1/scheduling/runs", headers=make_user(role)[1]).status_code == 403
    assert client.post("/v1/scheduling/runs", headers=o["staff"]).status_code == 201


def test_workday_arithmetic():
    from b1.scheduling.calendar import Params, add_workdays, sub_workdays

    p = Params(holidays=["2027-01-01"])
    thu = dt.datetime(2026, 12, 31, 10, 0, tzinfo=TZ)  # thứ Năm; 1/1 nghỉ lễ; 2–3/1 cuối tuần
    assert add_workdays(thu, 3, p).date() == dt.date(2027, 1, 6)
    assert sub_workdays(dt.date(2027, 1, 11), 3, p) == dt.date(2027, 1, 6)  # thứ Hai → thứ Tư tuần trước


def test_service_rejects_bad_ownership(app, onboard):
    a, b = onboard(), onboard()
    from b1.scheduling.service import RequestError

    with pytest.raises(RequestError):
        app.state.scheduling.submit(SYSTEM, b["customer_id"], aircraft_id=a["aircraft_id"], pilot_id=a["pilot_id"],
                                    priority_group=3, bvlos=True, block_ids=["block-1"],
                                    items=[ItemIn(today() + dt.timedelta(days=10), 0)])
