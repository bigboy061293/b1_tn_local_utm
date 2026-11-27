"""Nhật ký truy vết (F-12): chỉ ghi thêm, nối chuỗi mã băm, phát hiện được sửa đổi."""
from __future__ import annotations

import pytest
import sqlalchemy as sa

from b1.audit.service import SYSTEM
from b1.identity.roles import Role


def test_chain_verifies_and_is_append_only(app):
    audit = app.state.audit
    for i in range(5):
        audit.record(SYSTEM, "test.event", target_type="t", target_id=i, after={"i": i, "chữ": "có dấu"})
    assert audit.verify_chain()["ok"] is True
    with pytest.raises(sa.exc.DBAPIError):
        with app.state.engine.begin() as c:
            c.execute(sa.text("UPDATE audit.audit_log SET outcome='ok' WHERE action='test.event'"))
    with pytest.raises(sa.exc.DBAPIError):
        with app.state.engine.begin() as c:
            c.execute(sa.text("DELETE FROM audit.audit_log WHERE action='test.event'"))
    with pytest.raises(sa.exc.DBAPIError):
        with app.state.engine.begin() as c:
            c.execute(sa.text("TRUNCATE audit.audit_log"))


def test_tampering_is_detected(pg_url, signing_key):
    """Kẻ có quyền quản trị CSDL tắt trigger để sửa: chuỗi đứt đúng tại bản ghi bị sửa."""
    from b1.app import create_app
    from b1.config import Settings

    # Dùng CSDL riêng để không làm hỏng chuỗi của các bài khác.
    admin = sa.create_engine(pg_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.exec_driver_sql('CREATE DATABASE "b1_tamper_test"') if not c.execute(
            sa.text("SELECT 1 FROM pg_database WHERE datname='b1_tamper_test'")).first() else None
    url = pg_url.rsplit("/", 1)[0] + "/b1_tamper_test"
    try:
        app = create_app(Settings(database_url=url, jwks_file=signing_key[1]))
        audit = app.state.audit
        rows = [audit.record(SYSTEM, "test.tamper", target_id=i, after={"v": i}) for i in range(4)]
        with app.state.engine.begin() as c:
            c.execute(sa.text("ALTER TABLE audit.audit_log DISABLE TRIGGER audit_log_append_only"))
            c.execute(sa.text("UPDATE audit.audit_log SET after = '{\"v\": 99}' WHERE seq = :s"), {"s": rows[2].seq})
            c.execute(sa.text("ALTER TABLE audit.audit_log ENABLE TRIGGER audit_log_append_only"))
        r = audit.verify_chain()
        assert r == {"ok": False, "records": rows[2].seq - 1, "first_bad_seq": rows[2].seq}
        app.state.engine.dispose()
    finally:
        with admin.connect() as c:
            c.exec_driver_sql('DROP DATABASE IF EXISTS "b1_tamper_test" WITH (FORCE)')
        admin.dispose()


def test_audit_api_permissions(client, make_user):
    _, auditor = make_user(Role.AUDITOR)
    _, customer = make_user(Role.CUSTOMER_OPERATOR)
    assert client.get("/v1/audit/verify", headers=auditor).json()["ok"] is True
    assert client.get("/v1/audit", headers=customer).status_code == 403
    items = client.get("/v1/audit?action=audit.read", headers=auditor).json()["items"]
    assert items and items[-1]["outcome"] == "denied"
