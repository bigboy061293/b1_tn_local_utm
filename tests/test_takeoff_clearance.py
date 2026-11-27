"""F-04 Cho phép cất cánh: danh mục kiểm tra, điều kiện khối và khung giờ, ca trực, ký điện tử (Đặc tả B1 v0.1, Mục 5)."""
from __future__ import annotations

import time

import jwt
import pytest

from b1.identity.roles import Role

ITEMS = ("weather", "ris_stations", "network", "conformance_monitoring", "emergency_plan")
BODY = {**{k: {"ok": True} for k in ITEMS}, "confirm": True}


def clear(client, fid, h, body=BODY):
    return client.post(f"/v1/flights/{fid}/takeoff-clearance", headers=h, json=body)


def failed_codes(r) -> set[str]:
    assert r.status_code == 409, r.text
    return {x["code"] for x in r.json()["detail"]["failed"]}


def test_success_produces_signed_record(client, app, make_user, ready_flight):
    fid = ready_flight()
    uid, h = make_user(Role.DUTY_SAFETY_OFFICER)
    r = clear(client, fid, h, {**BODY, "weather": {"ok": True, "note": "gió 5 m/s"}, "statement": "Đủ điều kiện"})
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "cleared"
    _, aud = make_user(Role.AUDITOR)
    c = client.get(f"/v1/flights/{fid}/clearance", headers=aud).json()
    assert c["signature_valid"] is True
    rec = c["record"]
    assert rec["signer"]["user_id"] == str(uid) and "otp" in rec["signer"]["amr"]
    assert rec["checklist"]["weather"]["note"] == "gió 5 m/s" and all(rec["checklist"][k]["ok"] for k in ITEMS)
    assert {p["code"] for p in rec["preconditions"]} == {"WINDOW", "BLOCKS_ACTIVE", "ON_DUTY", "CHECKLIST", "SIGNER_REAUTH"}
    # bên ngoài kiểm được chữ ký bằng khoá công khai công bố
    jwks = client.get("/v1/signing-keys").json()
    key = jwt.PyJWK(jwks["keys"][0])
    claims = jwt.decode(c["signature"], key.key, algorithms=["ES256"])
    assert claims["sha256"] == c["sha256"] and claims["record"] == rec


def test_tampered_record_is_detected(client, app, make_user, ready_flight):
    from sqlalchemy import update

    from b1.operations.models import Clearance

    fid = ready_flight()
    _, h = make_user(Role.DUTY_SAFETY_OFFICER)
    assert clear(client, fid, h).status_code == 200
    with app.state.sessionmaker() as s:
        c = s.query(Clearance).filter_by(flight_id=fid).one()
        rec = dict(c.record, statement="sửa sau khi ký")
        s.execute(update(Clearance).where(Clearance.id == c.id).values(record=rec))
        s.commit()
    _, aud = make_user(Role.AUDITOR)
    assert client.get(f"/v1/flights/{fid}/clearance", headers=aud).json()["signature_valid"] is False


@pytest.mark.parametrize("item", ITEMS)
def test_any_failed_checklist_item_blocks(client, app, make_user, ready_flight, item):
    fid = ready_flight()
    _, h = make_user(Role.DUTY_SAFETY_OFFICER)
    r = clear(client, fid, h, {**BODY, item: {"ok": False, "note": "chưa đạt"}})
    assert failed_codes(r) == {"CHECKLIST"}
    logged = app.state.audit.list(action="flight.clear_takeoff", target_id=fid)
    assert [x.outcome for x in logged] == ["rejected"]
    _, aud = make_user(Role.AUDITOR)
    assert client.get(f"/v1/flights/{fid}", headers=aud).json()["state"] == "scheduled"


def test_checklist_and_confirmation_required(client, make_user, ready_flight):
    fid = ready_flight()
    _, h = make_user(Role.DUTY_SAFETY_OFFICER)
    assert client.post(f"/v1/flights/{fid}/takeoff-clearance", headers=h).status_code == 422
    assert clear(client, fid, h, {k: v for k, v in BODY.items() if k != "network"}).status_code == 422
    assert clear(client, fid, h, {**BODY, "confirm": False}).status_code == 422


def test_too_early_or_after_window(client, make_user, ready_flight):
    _, h = make_user(Role.DUTY_SAFETY_OFFICER)
    assert failed_codes(clear(client, ready_flight(start_in_min=120), h)) == {"WINDOW"}
    assert failed_codes(clear(client, ready_flight(start_in_min=-40, minutes=30), h)) == {"WINDOW"}


def test_block_must_be_active_for_whole_window(client, make_user, ready_flight):
    _, h = make_user(Role.DUTY_SAFETY_OFFICER)
    assert failed_codes(clear(client, ready_flight(block_ids=("block-1", "block-2")), h)) == {"BLOCKS_ACTIVE"}
    # khối 1 kích hoạt đến 8 giờ sau; chuyến kéo dài quá đó thì không phủ trọn
    assert failed_codes(clear(client, ready_flight(start_in_min=10, minutes=9 * 60), h)) == {"BLOCKS_ACTIVE"}


def test_corridor_head_must_be_on_duty(client, make_user, put_on_duty, ready_flight):
    uid, h = make_user(Role.CORRIDOR_HEAD)
    fid = ready_flight()
    assert failed_codes(clear(client, fid, h)) == {"ON_DUTY"}
    put_on_duty(uid, "duty_safety_officer")  # ca của vai trò khác không tính
    assert failed_codes(clear(client, fid, h)) == {"ON_DUTY"}
    put_on_duty(uid, "corridor_head")
    assert clear(client, fid, h).status_code == 200


def test_signer_must_have_reauthenticated_recently(client, app, make_user, make_token, ready_flight):
    from b1.identity.models import UserAccount

    uid, _ = make_user(Role.DUTY_SAFETY_OFFICER)
    with app.state.sessionmaker() as s:
        sub = s.get(UserAccount, uid).subject
    fid = ready_flight()
    stale = make_token(sub, auth_time=int(time.time()) - 3600)
    assert failed_codes(clear(client, fid, {"Authorization": f"Bearer {stale}"})) == {"SIGNER_REAUTH"}
    missing = make_token(sub, auth_time=None)
    assert failed_codes(clear(client, fid, {"Authorization": f"Bearer {missing}"})) == {"SIGNER_REAUTH"}


def test_duty_roster_management(client, make_user):
    uid, _ = make_user(Role.CORRIDOR_HEAD)
    _, head = make_user(Role.CORRIDOR_HEAD)
    _, it = make_user(Role.IT_ENGINEER)
    body = {"user_id": str(uid), "role": "corridor_head", "starts_at": "2027-01-05T00:00:00+07:00",
            "ends_at": "2027-01-05T12:00:00+07:00"}
    assert client.post("/v1/duty/shifts", headers=it, json=body).status_code == 403
    r = client.post("/v1/duty/shifts", headers=head, json=body)
    assert r.status_code == 201, r.text
    assert client.post("/v1/duty/shifts", headers=head, json={**body, "ends_at": "2027-01-07T00:00:00+07:00"}).status_code == 422
    listed = client.get("/v1/duty/shifts", headers=head, params={"at": "2027-01-05T03:00:00Z"}).json()["items"]
    assert [x["id"] for x in listed] == [r.json()["id"]]
    assert client.post(f"/v1/duty/shifts/{r.json()['id']}/cancel", headers=head).json()["cancelled"] is True
