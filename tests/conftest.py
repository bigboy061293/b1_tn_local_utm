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

    def _make(*roles, mfa: bool = True, expires_at: dt.datetime | None = None, status: str = "active"):
        sub = f"u-{uuid.uuid4().hex[:10]}"
        with app.state.sessionmaker() as s:
            u = UserAccount(issuer=ISSUER, subject=sub, display_name=sub, expires_at=expires_at, status=status)
            s.add(u)
            s.flush()
            s.add_all([RoleAssignment(user_id=u.id, role=getattr(r, "value", r)) for r in roles])
            s.commit()
            uid = u.id
        return uid, {"Authorization": f"Bearer {make_token(sub, mfa=mfa)}"}
    return _make
