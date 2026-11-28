"""Dụng cụ kiểm thử cho dịch vụ B1: CSDL PostgreSQL tạm, khoá ký giả lập bên cấp danh tính, tạo người dùng theo vai trò.

Cần PostgreSQL. Mặc định postgresql://b1:b1@localhost:5432 (docker run -e POSTGRES_USER=b1 -e POSTGRES_PASSWORD=b1
-p 5432:5432 postgres:17-alpine); đổi bằng B1_TEST_PG_ADMIN_URL. Không có PostgreSQL thì bỏ qua các bài cần CSDL,
trừ khi đặt B1_TEST_REQUIRE_DB=1 (CI đặt biến này để không bỏ qua âm thầm).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
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

    key_file = str(pathlib.Path(signing_key[1]).parent / "b1_signing_key.pem")
    return create_app(Settings(database_url=pg_url, oidc_issuer=ISSUER, oidc_audience=AUDIENCE, jwks_file=signing_key[1],
                               signing_key_file=key_file))


@pytest.fixture(scope="session")
def client(app):
    from fastapi.testclient import TestClient

    return TestClient(app)


@pytest.fixture(scope="session")
def make_token(signing_key):
    def _make(sub: str, mfa: bool = True, issuer: str = ISSUER, ttl: int = 600, **extra) -> str:
        now = int(time.time())
        claims = {"iss": issuer, "aud": AUDIENCE, "sub": sub, "iat": now, "exp": now + ttl,
                  "amr": ["pwd", "otp"] if mfa else ["pwd"], "auth_time": now - 30, **extra}
        return jwt.encode(claims, signing_key[0], algorithm="RS256", headers={"kid": KID})
    return _make


@pytest.fixture(scope="session")
def make_user(app, make_token):
    """Tạo người dùng với các vai trò cho trước, trả (user_id, headers)."""
    from b1.identity.models import RoleAssignment, UserAccount

    def _make(*roles, mfa: bool = True, expires_at: dt.datetime | None = None, status: str = "active",
              org_id: uuid.UUID | None = None, **claims):
        sub = f"u-{uuid.uuid4().hex[:10]}"
        with app.state.sessionmaker() as s:
            u = UserAccount(issuer=ISSUER, subject=sub, display_name=sub, expires_at=expires_at, status=status, org_id=org_id)
            s.add(u)
            s.flush()
            s.add_all([RoleAssignment(user_id=u.id, role=getattr(r, "value", r)) for r in roles])
            s.commit()
            uid = u.id
        return uid, {"Authorization": f"Bearer {make_token(sub, mfa=mfa, **claims)}"}
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


CHECKLIST_OK = {k: {"ok": True} for k in ("weather", "ris_stations", "network", "conformance_monitoring", "emergency_plan")}
CLEARANCE_BODY = {**CHECKLIST_OK, "confirm": True}


@pytest.fixture(scope="session")
def live_block(app):
    """Khối 1 đã hiệp đồng và kích hoạt từ 1 giờ trước đến 8 giờ sau (dùng chung cho các bài cho phép cất cánh)."""
    from b1.airspace import package as pkg
    from b1.audit.service import SYSTEM

    sp = app.state.airspace
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    a = sp.create_activation(SYSTEM, "block-1", now - dt.timedelta(hours=1), now + dt.timedelta(hours=8), [])
    sp.coordinate(SYSTEM, a.id, result="approved", package_sha256=pkg.sha256(sp.package(a.id)), reference="CV-TEST/1",
                  counterpart="Kiểm thử")
    return sp.activate(SYSTEM, a.id)


@pytest.fixture(scope="session")
def ready_flight(app, live_block):
    """Tạo chuyến đã xếp lịch trong khối 1 đang kích hoạt, bắt đầu sau `start_in_min` phút."""
    def _make(start_in_min: int = 10, minutes: int = 30, block_ids=("block-1",)) -> str:
        now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
        st = now + dt.timedelta(minutes=start_in_min)
        return str(app.state.operations.create_flight(
            request_ref=f"HL-{now:%Y%m%d}-{uuid.uuid4().int % 10_000:04d}", uas_id="SIM0001B1TN", customer_id=None,
            starts_at=st, ends_at=st + dt.timedelta(minutes=minutes), block_ids=list(block_ids), bvlos=True))
    return _make


@pytest.fixture(scope="session")
def put_on_duty(app):
    from b1.operations.models import DutyShift

    def _put(user_id, role: str = "corridor_head", hours: float = 4):
        now = dt.datetime.now(dt.timezone.utc)
        with app.state.sessionmaker() as s:
            s.add(DutyShift(user_id=user_id, role=role, starts_at=now - dt.timedelta(minutes=5), ends_at=now + dt.timedelta(hours=hours)))
            s.commit()
    return _put


def _centerline():
    from b1 import geo
    f = json.loads((pathlib.Path(__file__).resolve().parent.parent / "config/corridor/centerline.geojson").read_text())["features"][0]
    return f["geometry"]["coordinates"], geo


def corridor_point(km: float, offset_m: float = 0.0) -> tuple[float, float]:
    """Điểm (lon, lat) trên tim tuyến tại Km `km` (đo theo đường thẳng giữa các đỉnh), lệch ngang `offset_m` sang phải."""
    import math
    coords, geo = _centerline()
    d = km * 1000.0
    for a, b in zip(coords, coords[1:]):
        seg = geo.distance_m(tuple(a), tuple(b))
        if d <= seg or b == coords[-1]:
            f = d / seg
            lon, lat = a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f
            kx, ky = geo.scale(lat)
            dx, dy = (b[0] - a[0]) * kx, (b[1] - a[1]) * ky
            n = math.hypot(dx, dy)
            nx, ny = dy / n, -dx / n
            return lon + nx * offset_m / kx, lat + ny * offset_m / ky
        d -= seg
    raise ValueError(km)


def make_sample(fid, uas, t: dt.datetime, km: float = 2.0, offset_m: float = 0.0, **over) -> dict:
    lon, lat = corridor_point(km, offset_m)
    s = {"flightId": str(fid), "uasId": uas, "timeSource": t.astimezone(dt.timezone.utc).isoformat(timespec="milliseconds")
         .replace("+00:00", "Z"), "position": {"lat": round(lat, 7), "lon": round(lon, 7)},
         "altitude": {"value": 120.0, "reference": "AGL"},
         "velocity": {"groundSpeed": 15.0, "verticalSpeed": 0.0, "track": 318.0},
         "gnss": {"fixType": "3d", "satellites": 16, "hdop": 0.8}, "energy": {"batteryPercent": 80.0, "voltage": 48.0},
         "flightMode": {"mode": "AUTO", "autonomy": "automatic"}, "armed": True, "safetyFlags": [], "c2Link": {"state": "ok"}}
    for k, v in over.items():
        s[k] = v
    return s


@pytest.fixture(scope="session")
def flying(app, client, make_user, onboard, live_block):
    """Chuyến của một khách đã onboard, trong khối 1 đang kích hoạt, đã được cho phép cất cánh.

    Trả dict: fid, uas, tel (headers token khách có phạm vi telemetry:write), o (kết quả onboard).
    """
    def _make(start_in_min: int = 5, minutes: int = 60) -> dict:
        o = onboard()
        uas = client.get(f"/v1/registry/aircraft/{o['aircraft_id']}", headers=o["customer"]).json()["uas_id"]
        now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
        st = now + dt.timedelta(minutes=start_in_min)
        fid = app.state.operations.create_flight(request_ref=f"HL-{now:%Y%m%d}-{uuid.uuid4().int % 10_000:04d}", uas_id=uas,
                                                 customer_id=uuid.UUID(o["customer_id"]), starts_at=st,
                                                 ends_at=st + dt.timedelta(minutes=minutes), block_ids=["block-1"], bvlos=True)
        from b1.identity.roles import Role
        _, dso = make_user(Role.DUTY_SAFETY_OFFICER)
        r = client.post(f"/v1/flights/{fid}/takeoff-clearance", headers=dso, json=CLEARANCE_BODY)
        assert r.status_code == 200, r.text
        _, tel = make_user(Role.CUSTOMER_OPERATOR, org_id=o["org_id"], scope="telemetry:write")
        return {"fid": fid, "uas": uas, "tel": tel, "o": o}
    return _make
