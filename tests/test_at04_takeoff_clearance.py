"""AT-04 Quyền cho phép cất cánh: chỉ 2 vai trò được phép; các vai trò khác bị từ chối (Đặc tả B1 v0.1, Mục 9).

Thêm: thao tác đặc quyền phải có xác thực đa yếu tố, và mọi lần bị từ chối có trong nhật ký truy vết.
"""
from __future__ import annotations

import pytest

from b1.identity.roles import Role

ALLOWED = {Role.CORRIDOR_HEAD, Role.DUTY_SAFETY_OFFICER}
BODY = {**{k: {"ok": True} for k in ("weather", "ris_stations", "network", "conformance_monitoring", "emergency_plan")},
        "confirm": True}


@pytest.fixture()
def flight(ready_flight):
    return ready_flight()


@pytest.mark.parametrize("role", list(Role), ids=lambda r: r.value)
def test_only_two_roles_can_clear_takeoff(client, app, make_user, put_on_duty, flight, role):
    uid, h = make_user(role)
    put_on_duty(uid, role.value)  # có ca trực cũng không thay được quyền
    r = client.post(f"/v1/flights/{flight}/takeoff-clearance", headers=h, json=BODY)
    if role in ALLOWED:
        assert r.status_code == 200, r.text
        assert r.json()["state"] == "cleared" and r.json()["takeoff_cleared_by"] == str(uid)
        logged = app.state.audit.list(action="flight.clear_takeoff", target_id=flight)
        assert [x.outcome for x in logged] == ["ok"]
    else:
        assert r.status_code == 403
        logged = app.state.audit.list(action="flight.clear_takeoff", target_id=flight)
        assert len(logged) == 1 and logged[0].outcome == "denied" and logged[0].actor_id == uid
        assert client.get(f"/v1/flights/{flight}", headers=make_user(Role.AUDITOR)[1]).json()["state"] == "scheduled"


def test_user_without_roles_is_denied(client, make_user, flight):
    _, h = make_user()
    assert client.post(f"/v1/flights/{flight}/takeoff-clearance", headers=h, json=BODY).status_code == 403


def test_allowed_role_without_mfa_is_denied(client, app, make_user, flight):
    _, h = make_user(Role.DUTY_SAFETY_OFFICER, mfa=False)
    r = client.post(f"/v1/flights/{flight}/takeoff-clearance", headers=h, json=BODY)
    assert r.status_code == 403 and "đa yếu tố" in r.json()["detail"]


def test_expired_or_disabled_account_is_denied(client, make_user, flight):
    import datetime as dt

    _, h1 = make_user(Role.CORRIDOR_HEAD, expires_at=dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc))
    _, h2 = make_user(Role.CORRIDOR_HEAD, status="disabled")
    assert client.post(f"/v1/flights/{flight}/takeoff-clearance", headers=h1, json=BODY).status_code == 403
    assert client.post(f"/v1/flights/{flight}/takeoff-clearance", headers=h2, json=BODY).status_code == 403


def test_cannot_clear_twice(client, make_user, flight):
    _, h = make_user(Role.DUTY_SAFETY_OFFICER)
    assert client.post(f"/v1/flights/{flight}/takeoff-clearance", headers=h, json=BODY).status_code == 200
    assert client.post(f"/v1/flights/{flight}/takeoff-clearance", headers=h, json=BODY).status_code == 409


def test_token_checks(client, make_token, make_user, flight):
    assert client.post(f"/v1/flights/{flight}/takeoff-clearance", json=BODY).status_code == 401
    bad_iss = make_token("x", issuer="https://evil.example")
    assert client.post(f"/v1/flights/{flight}/takeoff-clearance", headers={"Authorization": f"Bearer {bad_iss}"}, json=BODY).status_code == 401
    expired = make_token("x", ttl=-120)
    assert client.post(f"/v1/flights/{flight}/takeoff-clearance", headers={"Authorization": f"Bearer {expired}"}, json=BODY).status_code == 401
    unknown = make_token("not-provisioned")
    assert client.post(f"/v1/flights/{flight}/takeoff-clearance", headers={"Authorization": f"Bearer {unknown}"}, json=BODY).status_code == 403
