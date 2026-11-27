"""I-01: cổng đăng ký bay của tỉnh gửi hồ sơ vào B1. Hồ sơ đi đúng luồng kiểm điều kiện và chặn như cổng khách hàng."""
from __future__ import annotations

import datetime as dt
import uuid

import pytest
import sqlalchemy as sa

from b1.identity.roles import Role
from b1.scheduling.calendar import TZ
from tests.conftest import ISSUER


@pytest.fixture(autouse=True)
def clean(app):
    with app.state.engine.begin() as c:
        c.execute(sa.text("TRUNCATE scheduling.request_item, scheduling.flight_request, scheduling.block_reason, "
                          "scheduling.scheduling_run, scheduling.param, scheduling.queue_counter, operations.flight, "
                          "gateway.application"))


@pytest.fixture()
def province(app, make_token):
    from b1.identity.models import RoleAssignment, UserAccount

    sub = f"svc-province-{uuid.uuid4().hex[:6]}"
    with app.state.sessionmaker() as s:
        u = UserAccount(issuer=ISSUER, subject=sub, display_name="Cổng đăng ký bay tỉnh Tây Ninh")
        s.add(u)
        s.flush()
        s.add(RoleAssignment(user_id=u.id, role=Role.PROVINCE_PORTAL.value))
        s.commit()

    def headers(scope="province:applications.write province:applications.read"):
        return {"Authorization": f"Bearer {make_token(sub, mfa=False, scope=scope)}"}
    return headers


def application(o_unified: str, uas: str, docs: list[dict], ref: str | None = None, day_offset: int = 10, blocks=("block-1",)):
    d = str(dt.datetime.now(TZ).date() + dt.timedelta(days=day_offset))
    return {"externalRef": ref or f"TN-{uuid.uuid4().hex[:8]}", "customer": {"unifiedId": o_unified, "name": "Khách qua cổng tỉnh"},
            "pilot": {"unifiedId": "079123456789", "licenceNo": "GPDK-77"},
            "aircraft": {"uasId": uas, "vnRegistrationNo": f"VN-I01-{uas[-4:]}", "mtowKg": 12, "maxSpeedMs": 25},
            "priorityGroup": 3, "bvlos": True, "requestedSlots": [{"date": d, "slotIndex": 1, "blockIds": list(blocks)}],
            "documents": docs}


def unified_customer(client, onboard):
    o = onboard()
    uid = f"MST-{uuid.uuid4().hex[:8]}"
    with client.app.state.engine.begin() as c:
        c.execute(sa.text("UPDATE registry.customer SET unified_id = :u WHERE id = :i"), {"u": uid, "i": o["customer_id"]})
    return o, uid


def test_new_aircraft_from_province_is_blocked_until_verified(client, app, onboard, province):
    o, uid = unified_customer(client, onboard)
    docs = [{"kind": "registration", "number": "DK-1", "validTo": "2028-12-31"},
            {"kind": "technical_status", "number": "KT-1", "validTo": "2028-12-31"}]
    r = client.post("/gateway/v1/applications", json=application(uid, "1596F3YBI01000001", docs), headers=province())
    assert r.status_code == 201, r.text
    j = r.json()
    assert j["state"] == "incomplete" and j["provisional"] is True
    rules = {b["rule"] for b in j["blockReasons"]}
    assert {"REGISTRATION", "TECHNICAL_STATUS", "CONFORMANCE", "FLIGHT_MGMT_DEVICE"} <= rules  # giấy tờ mới chỉ "đã nộp"
    docs_db = client.get("/v1/registry/documents", headers=o["staff"], params={"customer_id": o["customer_id"]}).json()["items"]
    assert {d["kind"]: d["status"] for d in docs_db if d["note"] and d["note"].startswith("I-01")} == {
        "registration_certificate": "submitted", "technical_status_certificate": "submitted"}
    assert client.get(f"/gateway/v1/applications/{j['applicationId']}", headers=province()).json()["state"] == "incomplete"


def test_known_complete_aircraft_is_queued_then_scheduled(client, onboard, province):
    o, uid = unified_customer(client, onboard)
    uas = client.get(f"/v1/registry/aircraft/{o['aircraft_id']}", headers=o["staff"]).json()["uas_id"]
    pilot_uid = "079123456789"
    with client.app.state.engine.begin() as c:
        c.execute(sa.text("UPDATE registry.pilot SET unified_id = :u WHERE id = :i"), {"u": pilot_uid, "i": o["pilot_id"]})
    body = application(uid, uas, [])
    j = client.post("/gateway/v1/applications", json=body, headers=province()).json()
    assert j["state"] == "queued" and j["queueNo"].startswith("HL-")
    client.post("/v1/scheduling/runs", headers=o["staff"])
    k = client.get(f"/gateway/v1/applications/{j['applicationId']}", headers=province()).json()
    assert k["state"] == "scheduled" and len(k["scheduledSlots"]) == 1
    again = client.post("/gateway/v1/applications", json=body, headers=province())  # gửi lại: không tạo trùng
    assert again.status_code == 200 and again.json()["applicationId"] == j["applicationId"]


def test_unknown_customer_rejected_and_logged(client, app, province):
    j = client.post("/gateway/v1/applications", json=application("MST-NOPE", "1596F3YBI01000009", []), headers=province()).json()
    assert j["state"] == "rejected" and j["blockReasons"][0]["rule"] == "CUSTOMER_UNKNOWN"
    assert app.state.audit.list(action="i01.application.submit", target_id=j["applicationId"])[0].outcome == "rejected"


def test_aircraft_of_other_customer_rejected(client, onboard, province):
    a = onboard()
    _, uid_b = unified_customer(client, onboard)
    uas_a = client.get(f"/v1/registry/aircraft/{a['aircraft_id']}", headers=a["staff"]).json()["uas_id"]
    j = client.post("/gateway/v1/applications", json=application(uid_b, uas_a, []), headers=province()).json()
    assert j["state"] == "rejected" and j["blockReasons"][0]["rule"] == "AIRCRAFT_OTHER_CUSTOMER"


def test_closed_block_rejected(client, onboard, province):
    o, uid = unified_customer(client, onboard)
    j = client.post("/gateway/v1/applications", json=application(uid, "1596F3YBI01000005", [], blocks=("block-3",)),
                    headers=province()).json()
    assert j["state"] == "rejected" and j["blockReasons"][0]["rule"] == "INVALID_REQUEST"


def test_scopes_and_roles(client, onboard, province, make_user):
    o, uid = unified_customer(client, onboard)
    body = application(uid, "1596F3YBI01000007", [])
    assert client.post("/gateway/v1/applications", json=body, headers=province("province:applications.read")).status_code == 403
    assert client.post("/gateway/v1/applications", json=body, headers=o["customer"]).status_code == 403
    assert client.post("/gateway/v1/applications", json=body, headers=make_user(Role.CORRIDOR_HEAD)[1]).status_code == 403
    j = client.post("/gateway/v1/applications", json=body, headers=province()).json()
    assert client.get(f"/gateway/v1/applications/{j['applicationId']}", headers=province("province:applications.write")).status_code == 403
