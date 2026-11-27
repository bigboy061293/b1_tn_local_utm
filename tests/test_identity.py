"""Quản trị người dùng (F-12): chỉ kỹ sư CNTT quản lý tài khoản; mọi thay đổi vai trò có trước/sau trong nhật ký."""
from __future__ import annotations

from b1.identity.roles import Role
from tests.conftest import ISSUER


def test_user_lifecycle(client, app, make_user, make_token):
    _, it = make_user(Role.IT_ENGINEER)
    r = client.post("/v1/users", json={"issuer": ISSUER, "subject": "pilot-head-1", "display_name": "Trưởng bộ phận",
                                       "roles": ["corridor_head"]}, headers=it)
    assert r.status_code == 201, r.text
    uid = r.json()["id"]
    me = client.get("/v1/me", headers={"Authorization": f"Bearer {make_token('pilot-head-1')}"}).json()
    assert me["roles"] == ["corridor_head"] and me["mfa"] is True

    r = client.post(f"/v1/users/{uid}/roles", json={"role": "duty_safety_officer"}, headers=it)
    assert sorted(x["role"] for x in r.json()["roles"]) == ["corridor_head", "duty_safety_officer"]
    rid = next(x["id"] for x in r.json()["roles"] if x["role"] == "corridor_head")
    r = client.delete(f"/v1/users/{uid}/roles/{rid}", headers=it)
    assert [x["role"] for x in r.json()["roles"]] == ["duty_safety_officer"]

    log = app.state.audit.list(action="identity.role.revoke", target_id=uid)
    assert len(log) == 1 and len(log[0].before["roles"]) == 2 and len(log[0].after["roles"]) == 1

    assert client.post(f"/v1/users/{uid}/disable", headers=it).json()["status"] == "disabled"
    assert client.get("/v1/me", headers={"Authorization": f"Bearer {make_token('pilot-head-1')}"}).status_code == 403


def test_only_it_engineer_manages_users(client, make_user):
    for role in Role:
        if role is Role.IT_ENGINEER:
            continue
        _, h = make_user(role)
        r = client.post("/v1/users", json={"issuer": ISSUER, "subject": f"x-{role.value}", "display_name": "x"}, headers=h)
        assert r.status_code == 403, role


def test_disclaimers_public(client):
    assert len(client.get("/v1/disclaimers").json()["items"]) == 4
