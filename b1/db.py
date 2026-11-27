"""Kết nối CSDL. Mỗi dịch vụ có schema riêng; dịch vụ khác chỉ gọi qua API Python hoặc HTTP của nó (AT-13)."""
from __future__ import annotations

from collections.abc import Iterator

from fastapi import Request
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

SCHEMAS = ("identity", "audit", "registry", "scheduling", "operations", "notify", "gateway", "airspace")


class Base(DeclarativeBase):
    pass


def make_engine(url: str) -> Engine:
    return create_engine(url, pool_pre_ping=True)


def make_sessionmaker(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)


AUDIT_GUARD_SQL = """
CREATE OR REPLACE FUNCTION audit.forbid_change() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'audit.audit_log chỉ được ghi thêm (%%)', TG_OP;
END $$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS audit_log_append_only ON audit.audit_log;
CREATE TRIGGER audit_log_append_only BEFORE UPDATE OR DELETE ON audit.audit_log
  FOR EACH ROW EXECUTE FUNCTION audit.forbid_change();
DROP TRIGGER IF EXISTS audit_log_no_truncate ON audit.audit_log;
CREATE TRIGGER audit_log_no_truncate BEFORE TRUNCATE ON audit.audit_log
  FOR EACH STATEMENT EXECUTE FUNCTION audit.forbid_change();
"""


def init_db(engine: Engine) -> None:
    """Tạo schema và bảng. Khi lược đồ ổn định sẽ chuyển sang migration (Alembic)."""
    from b1.audit import models as _a  # noqa: F401  đăng ký bảng vào Base.metadata
    from b1.identity import models as _i  # noqa: F401
    from b1.operations import models as _o  # noqa: F401
    from b1.registry import models as _r  # noqa: F401
    from b1.scheduling import models as _s  # noqa: F401
    from b1.notify import models as _n  # noqa: F401
    from b1.gateway import models as _g  # noqa: F401
    from b1.airspace import models as _sp  # noqa: F401

    with engine.begin() as c:
        for s in SCHEMAS:
            c.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{s}"'))
    Base.metadata.create_all(engine)
    with engine.begin() as c:
        c.exec_driver_sql(AUDIT_GUARD_SQL)


def get_session(request: Request) -> Iterator[Session]:
    with request.app.state.sessionmaker() as s:
        yield s
