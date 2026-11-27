"""Dụng cụ kiểm thử cho dịch vụ B1: CSDL PostgreSQL tạm, khoá ký giả lập bên cấp danh tính, tạo người dùng theo vai trò.

Cần PostgreSQL. Mặc định postgresql://b1:b1@localhost:5432 (docker run -e POSTGRES_USER=b1 -e POSTGRES_PASSWORD=b1
-p 5432:5432 postgres:17-alpine); đổi bằng B1_TEST_PG_ADMIN_URL. Không có PostgreSQL thì bỏ qua các bài cần CSDL,
trừ khi đặt B1_TEST_REQUIRE_DB=1 (CI đặt biến này để không bỏ qua âm thầm).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import time
import uuid

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

ADMIN_URL = os.environ.get("B1_TEST_PG_ADMIN_URL", "postgresql+psycopg://b1:b1@localhost:5432/postgres")
ISSUER, AUDIENCE, KID = "https://idp.test/realms/b1", "b1-api", "test-key"


@pytest.fixture(scope="session")
def pg_url():
    sa = pytest.importorskip("sqlalchemy")
    try:
        admin = sa.create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
        with admin.connect() as c:
            name = f"b1_test_{uuid.uuid4().hex[:8]}"
            c.exec_driver_sql(f'CREATE DATABASE "{name}"')
    except Exception as e:  # noqa: BLE001
        if os.environ.get("B1_TEST_REQUIRE_DB") == "1":
            raise
        pytest.skip(f"không có PostgreSQL cho kiểm thử ({e.__class__.__name__})")
    yield ADMIN_URL.rsplit("/", 1)[0] + "/" + name
    with admin.connect() as c:
        c.exec_driver_sql(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    admin.dispose()


@pytest.fixture(scope="session")
def signing_key(tmp_path_factory):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk.update({"kid": KID, "use": "sig", "alg": "RS256"})
    path = tmp_path_factory.mktemp("jwks") / "jwks.json"
    path.write_text(json.dumps({"keys": [jwk]}))
    return key, str(path)


@pytest.fixture(scope="session")
def app(pg_url, signing_key):
    from b1.app import create_app
    from b1.config import Settings

    return create_app(Settings(database_url=pg_url, oidc_issuer=ISSUER, oidc_audience=AUDIENCE, jwks_file=signing_key[1]))


@pytest.fixture(scope="session")
def client(app):
    from fastapi.testclient import TestClient

    return TestClient(app)


@pytest.fixture(scope="session")
def make_token(signing_key):
    def _make(sub: str, mfa: bool = True, issuer: str = ISSUER, ttl: int = 600, **extra) -> str:
        now = int(time.time())
        claims = {"iss": issuer, "aud": AUDIENCE, "sub": sub, "iat": now, "exp": now + ttl,
                  "amr": ["pwd", "otp"] if mfa else ["pwd"], **extra}
        return jwt.encode(claims, signing_key[0], algorithm="RS256", headers={"kid": KID})
    return _make


@pytest.fixture(scope="session")
def make_user(app, make_token):
    """Tạo người dùng với các vai trò cho trước, trả (user_id, headers)."""
    from b1.identity.models import RoleAssignment, UserAccount

    def _make(*roles, mfa: bool = True, expires_at: dt.datetime | None = None, status: str = "active",
              org_id: uuid.UUID | None = None):
        sub = f"u-{uuid.uuid4().hex[:10]}"
        with app.state.sessionmaker() as s:
            u = UserAccount(issuer=ISSUER, subject=sub, display_name=sub, expires_at=expires_at, status=status, org_id=org_id)
            s.add(u)
            s.flush()
            s.add_all([RoleAssignment(user_id=u.id, role=getattr(r, "value", r)) for r in roles])
            s.commit()
            uid = u.id
        return uid, {"Authorization": f"Bearer {make_token(sub, mfa=mfa)}"}
    return _make


@pytest.fixture(scope="session")
def onboard(app, client, make_user):
    """Lập một khách hàng có hồ sơ đầy đủ: phương tiện, người điều khiển, giấy tờ đã xác nhận, CT-1…8 đạt.

    Trả dict: customer (headers của khách), staff (headers của Trưởng bộ phận), customer_id, aircraft_id, pilot_id,
    docs (kind -> id). Tham số `skip` bỏ bớt bước để tạo hồ sơ thiếu.
    """
    from b1.identity.models import Organization
    from b1.identity.roles import Role

    _, staff = make_user(Role.CORRIDOR_HEAD)
    _, tech = make_user(Role.FLIGHT_TEST_TECHNICIAN)
    serials = iter(range(10_000, 99_999))

    def _onboard(skip: set[str] = frozenset(), mtow_kg: float = 25.0, in_testing: bool = True, tier: str = "C",
                 registration_via_i02: bool = False) -> dict:
        with app.state.sessionmaker() as s:
            org = Organization(name=f"KH {uuid.uuid4().hex[:6]}", kind="customer")
            s.add(org)
            s.commit()
            org_id = org.id
        _, cust = make_user(Role.CUSTOMER_OPERATOR, org_id=org_id)
        r = client.post("/v1/registry/customers", json={"org_id": str(org_id), "name": "Khách thử", "tier": tier}, headers=staff)
        assert r.status_code == 201, r.text
        cid = r.json()["id"]
        uas = "1596F3YB0000000002" if registration_via_i02 else f"1596F3YB{next(serials):010d}"
        ac = client.post("/v1/registry/aircraft", headers=cust, json={
            "uas_id": uas, "vn_registration_no": "VN-TEST-0002" if registration_via_i02 else f"VN-T-{uas[-6:]}",
            "mtow_kg": mtow_kg, "max_speed_ms": 30, "in_testing": in_testing, "config_ref": "fw-1.0/radio-A",
            "flight_mgmt_device_id": "QLHD-0002" if registration_via_i02 else f"QLHD-{uas[-6:]}"})
        assert ac.status_code == 201, ac.text
        aid = ac.json()["id"]
        pid = client.post("/v1/registry/pilots", headers=cust, json={"full_name": "Nguyễn Văn Lái"}).json()["id"]
        frm, to = "2026-01-01T00:00:00Z", "2028-12-31T23:59:59Z"
        plan = {"registration_certificate": ("aircraft", aid), "technical_status_certificate": ("aircraft", aid),
                "pilot_licence": ("pilot", pid), "test_permit": ("customer", cid), "insurance": ("customer", cid)}
        if registration_via_i02:
            plan.pop("registration_certificate")
            assert client.post(f"/v1/registry/aircraft/{aid}/verify-registration", headers=cust).json()["verified"]
        docs = {}
        for kind, (otype, oid) in plan.items():
            if kind in skip:
                continue
            d = client.post("/v1/registry/documents", headers=cust, json={
                "owner_type": otype, "owner_id": oid, "kind": kind, "number": f"{kind[:3].upper()}-1",
                "valid_from": frm, "valid_to": to}).json()
            docs[kind] = d["id"]
            if f"verify:{kind}" not in skip:
                assert client.post(f"/v1/registry/documents/{d['id']}/verify", headers=staff,
                                   json={"decision": "verified"}).status_code == 200
        if "device" not in skip and not registration_via_i02:
            assert client.post(f"/v1/registry/aircraft/{aid}/verify-device", headers=staff).status_code == 200
        for i in range(1, 9):
            if f"CT-{i}" in skip:
                continue
            r = client.post(f"/v1/registry/aircraft/{aid}/conformance", headers=tech, json={
                "test": f"CT-{i}", "passed": True, "tested_at": "2026-11-01T00:00:00Z"})
            assert r.status_code == 201, r.text
        return {"customer": cust, "staff": staff, "tech": tech, "customer_id": cid, "aircraft_id": aid, "pilot_id": pid,
                "docs": docs, "org_id": org_id}
    return _onboard
