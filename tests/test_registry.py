"""Đăng ký và hồ sơ (F-01): điều kiện chặn xếp lịch theo Đặc tả Mục 4.3, tách quyền khách hàng, I-02 giả lập."""
from __future__ import annotations

import pytest

from b1.identity.roles import Role

WINDOW = {"start": "2027-01-05T01:00:00Z", "end": "2027-01-05T03:00:00Z"}


def elig(client, o, headers=None, **kw):
    params = {"aircraft_id": o["aircraft_id"], "pilot_id": o["pilot_id"], **WINDOW, **kw}
    r = client.get("/v1/registry/eligibility", params=params, headers=headers or o["customer"])
    assert r.status_code == 200, r.text
    return r.json()


def rules(e) -> set[str]:
    return {m["rule"] for m in e["missing"]}


def test_complete_profile_is_eligible(client, onboard):
    o = onboard()
    assert elig(client, o) == {"eligible": True, "missing": []}
    assert elig(client, o, stage="final")["eligible"] is True


@pytest.mark.parametrize("skip,expected", [
    ({"registration_certificate"}, {"REGISTRATION"}),
    ({"verify:registration_certificate"}, {"REGISTRATION"}),  # nộp mà chưa được xác nhận thì không tính
    ({"technical_status_certificate"}, {"TECHNICAL_STATUS"}),
    ({"pilot_licence"}, {"PILOT_LICENCE"}),
    ({"device"}, {"FLIGHT_MGMT_DEVICE"}),
    ({"test_permit"}, {"PERMIT"}),
    ({"CT-5"}, {"CONFORMANCE"}),
])
def test_each_missing_item_blocks(client, onboard, skip, expected):
    assert rules(elig(client, onboard(skip=skip))) == expected


def test_insurance_only_checked_at_final_confirmation(client, onboard):
    o = onboard(skip={"insurance"})
    assert elig(client, o)["eligible"] is True
    assert rules(elig(client, o, stage="final")) == {"INSURANCE"}


def test_licence_rules_by_weight_and_vlos(client, onboard):
    light = onboard(skip={"pilot_licence"}, mtow_kg=1.5)
    assert elig(client, light, bvlos=False)["eligible"] is True        # dưới 2 kg, trong tầm nhìn
    assert rules(elig(client, light, bvlos=True)) == {"PILOT_LICENCE"}  # ngoài tầm nhìn
    heavy = onboard(skip={"pilot_licence"}, mtow_kg=2.0)
    assert rules(elig(client, heavy, bvlos=False)) == {"PILOT_LICENCE"}  # từ 2 kg


def test_technical_status_not_needed_when_not_in_testing(client, onboard):
    assert elig(client, onboard(skip={"technical_status_certificate"}, in_testing=False))["eligible"] is True


def test_document_must_cover_window(client, onboard):
    o = onboard()
    late = {"start": "2029-01-05T01:00:00Z", "end": "2029-01-05T03:00:00Z"}
    r = client.get("/v1/registry/eligibility", params={"aircraft_id": o["aircraft_id"], "pilot_id": o["pilot_id"], **late},
                   headers=o["customer"]).json()
    assert {"REGISTRATION", "TECHNICAL_STATUS", "PILOT_LICENCE", "PERMIT", "CONFORMANCE"} <= rules(r)


def test_config_change_invalidates_conformance(client, onboard):
    o = onboard()
    client.patch(f"/v1/registry/aircraft/{o['aircraft_id']}", json={"config_ref": "fw-1.1/radio-A"}, headers=o["customer"])
    assert rules(elig(client, o)) == {"CONFORMANCE"}


def test_device_change_requires_reverification(client, onboard):
    o = onboard()
    client.patch(f"/v1/registry/aircraft/{o['aircraft_id']}", json={"flight_mgmt_device_id": "QLHD-NEW"}, headers=o["customer"])
    assert rules(elig(client, o)) == {"FLIGHT_MGMT_DEVICE"}


def test_failed_latest_ct_blocks(client, onboard):
    o = onboard()
    client.post(f"/v1/registry/aircraft/{o['aircraft_id']}/conformance", headers=o["tech"],
                json={"test": "CT-2", "passed": False, "tested_at": "2026-11-20T00:00:00Z"})
    assert rules(elig(client, o)) == {"CONFORMANCE"}


def test_suspended_customer_blocked(client, onboard):
    o = onboard()
    client.patch(f"/v1/registry/customers/{o['customer_id']}", json={"status": "suspended"}, headers=o["staff"])
    assert rules(elig(client, o, headers=o["staff"])) == {"OWNERSHIP"}


def test_customer_isolation(client, app, onboard):
    a, b = onboard(), onboard()
    assert client.get(f"/v1/registry/aircraft/{a['aircraft_id']}", headers=b["customer"]).status_code == 403
    r = client.patch(f"/v1/registry/aircraft/{a['aircraft_id']}", json={"config_ref": "x"}, headers=b["customer"])
    assert r.status_code == 403
    r = client.post("/v1/registry/documents", headers=b["customer"], json={
        "owner_type": "aircraft", "owner_id": a["aircraft_id"], "kind": "insurance", "number": "X"})
    assert r.status_code == 403
    mine = client.get("/v1/registry/aircraft", headers=b["customer"]).json()["items"]
    assert [x["id"] for x in mine] == [b["aircraft_id"]]
    assert app.state.audit.list(action="registry.access", target_id=a["customer_id"])[-1].outcome == "denied"


def test_customer_cannot_verify_or_record_ct(client, onboard):
    o = onboard(skip={"verify:insurance"})
    r = client.post(f"/v1/registry/documents/{o['docs']['insurance']}/verify", headers=o["customer"], json={"decision": "verified"})
    assert r.status_code == 403
    r = client.post(f"/v1/registry/aircraft/{o['aircraft_id']}/conformance", headers=o["customer"],
                    json={"test": "CT-1", "passed": True, "tested_at": "2026-11-01T00:00:00Z"})
    assert r.status_code == 403
    r = client.patch(f"/v1/registry/customers/{o['customer_id']}", json={"tier": "A"}, headers=o["customer"])
    assert r.status_code == 403


def test_regulator_reads_but_cannot_write(client, make_user, onboard):
    o = onboard()
    _, reg = make_user(Role.REGULATOR)
    assert client.get(f"/v1/registry/aircraft/{o['aircraft_id']}", headers=reg).status_code == 200
    assert client.patch(f"/v1/registry/aircraft/{o['aircraft_id']}", json={"config_ref": "x"}, headers=reg).status_code == 403


def test_i02_mock_registration(client, app, onboard):
    o = onboard(registration_via_i02=True)
    assert elig(client, o)["eligible"] is True  # đăng ký và thiết bị quản lý hoạt động bay xác nhận qua I-02
    log = app.state.audit.list(action="registry.aircraft.verify_registration", target_id=o["aircraft_id"])
    assert log[-1].outcome == "ok" and log[-1].detail["connector"] == "I-02-mock"


@pytest.mark.parametrize("reg,serial,problem", [
    ("VN-TEST-0003", "1596F3YB0000000003", "trạng thái đăng ký: revoked"),
    ("VN-TEST-0001", "1596F3YBWRONGSERIAL", "số sê-ri không khớp UAS ID"),
    ("VN-NOPE-9999", "1596F3YB0000009999", "không có trong hệ thống đăng ký"),
])
def test_i02_mock_rejections(client, onboard, reg, serial, problem):
    o = onboard()
    ac = client.post("/v1/registry/aircraft", headers=o["customer"], json={
        "uas_id": serial, "vn_registration_no": reg, "mtow_kg": 5, "max_speed_ms": 20})
    assert ac.status_code == 201, ac.text
    r = client.post(f"/v1/registry/aircraft/{ac.json()['id']}/verify-registration", headers=o["customer"]).json()
    assert r["verified"] is False and problem in r["problems"]
