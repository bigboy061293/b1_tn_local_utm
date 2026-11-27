"""Cổng khách hàng và thông báo: trang tĩnh, cấu hình OIDC, thông báo kết quả xếp lịch và nhắc việc."""
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
                          "scheduling.scheduling_run, scheduling.param, scheduling.queue_counter, operations.flight, "
                          "notify.notification"))


def test_portal_served(client):
    r = client.get("/portal/")
    assert r.status_code == 200 and "Cổng khách hàng" in r.text
    assert client.get("/portal/static/app.js").status_code == 200
    cfg = client.get("/portal/config.json").json()
    assert cfg["client_id"] == "b1-portal" and cfg["issuer"].startswith("https://idp.test")


def kinds(client, o):
    return [n["kind"] for n in client.get("/v1/notifications", headers=o["customer"]).json()["items"]]


def test_notifications_follow_request(client, app, onboard):
    o = onboard(skip={"verify:test_permit"})
    d = str(dt.datetime.now(TZ).date() + dt.timedelta(days=10))
    body = {"aircraft_id": o["aircraft_id"], "pilot_id": o["pilot_id"], "block_ids": ["block-1"], "items": [{"day": d, "slot": 0}]}
    rid = client.post("/v1/scheduling/requests", json=body, headers=o["customer"]).json()["id"]
    assert kinds(client, o) == ["blocked_submit"]
    client.post(f"/v1/registry/documents/{o['docs']['test_permit']}/verify", json={"decision": "verified"}, headers=o["staff"])
    client.post(f"/v1/scheduling/requests/{rid}/recheck", headers=o["customer"])
    client.post("/v1/scheduling/runs", headers=o["staff"])
    assert kinds(client, o)[:2] == ["scheduled", "queued"]
    n = client.get("/v1/notifications?unread=true", headers=o["customer"]).json()["items"][0]
    assert client.post(f"/v1/notifications/{n['id']}/read", headers=o["customer"]).json()["read_at"]
    other = onboard()
    assert client.post(f"/v1/notifications/{n['id']}/read", headers=other["customer"]).status_code == 404
    assert client.get("/v1/notifications", headers=other["customer"]).json()["items"] == []


def test_reminders_t7_t3_t1_once_each(client, app, onboard):
    o = onboard()
    svc = app.state.scheduling
    day = dt.date(2027, 4, 20)
    svc.submit(SYSTEM, o["customer_id"], aircraft_id=o["aircraft_id"], pilot_id=o["pilot_id"], priority_group=3, bvlos=True,
               block_ids=["block-1"], items=[ItemIn(day, 1)], now=dt.datetime(2027, 4, 1, 9, tzinfo=TZ))
    for d in (1, 13, 14, 15, 17, 18, 19):  # chạy nhiều kỳ, kể cả nhiều kỳ trong cùng một mốc
        svc.run(SYSTEM, now=dt.datetime(2027, 4, d, 16, tzinfo=TZ))
        svc.run(SYSTEM, now=dt.datetime(2027, 4, d, 16, 30, tzinfo=TZ))
    ks = kinds(client, o)
    assert ks.count("reminder_t7") == 1 and ks.count("reminder_t3") == 1 and ks.count("reminder_t1") == 1
    assert ks.count("scheduled") == 1
