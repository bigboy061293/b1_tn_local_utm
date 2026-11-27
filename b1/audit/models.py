from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import BigInteger, DateTime, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from b1.db import Base


class AuditLog(Base):
    """Nhật ký truy vết chỉ ghi thêm, nối chuỗi mã băm (F-12). Trigger CSDL chặn UPDATE/DELETE/TRUNCATE."""
    __tablename__ = "audit_log"
    __table_args__ = {"schema": "audit"}
    seq: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    actor_id: Mapped[uuid.UUID | None] = mapped_column()          # None = hệ thống
    actor_label: Mapped[str] = mapped_column(String(300))          # sub hoặc "system"
    actor_roles: Mapped[list] = mapped_column(JSONB)
    action: Mapped[str] = mapped_column(String(80), index=True)
    outcome: Mapped[str] = mapped_column(String(20))               # ok | denied | rejected | error
    target_type: Mapped[str | None] = mapped_column(String(60))
    target_id: Mapped[str | None] = mapped_column(String(100), index=True)
    source_ip: Mapped[str | None] = mapped_column(String(64))
    before: Mapped[dict | None] = mapped_column(JSONB)
    after: Mapped[dict | None] = mapped_column(JSONB)
    detail: Mapped[dict | None] = mapped_column(JSONB)
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64), unique=True)
