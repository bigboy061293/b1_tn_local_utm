"""Cảnh báo và sự kiện giám sát (F-06). Hộp thư đi `monitoring.event` cho bản đồ và luồng cảnh báo."""
from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import BigInteger, DateTime, Index, Integer, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from b1.db import Base


class Alert(Base):
    """Một cảnh báo. Khử trùng theo `dedup_key` (chuyến hoặc đối tượng, loại, chủ thể) khi chưa giải toả.

    open → acknowledged (người vận hành xác nhận đã thấy) → resolved (điều kiện hết hoặc người vận hành đóng).
    """
    __tablename__ = "alert"
    __table_args__ = (Index("alert_open_dedup", "dedup_key", unique=True, postgresql_where=text("resolved_at IS NULL")),
                      {"schema": "monitoring"})
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    dedup_key: Mapped[str] = mapped_column(String(200))
    flight_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    subject: Mapped[str | None] = mapped_column(String(80))  # UAS ID, trạm, cờ, ICAO 24 bit
    kind: Mapped[str] = mapped_column(String(30))
    severity: Mapped[str] = mapped_column(String(10))  # info | warning | critical
    state: Mapped[str] = mapped_column(String(14), default="open")
    message: Mapped[str] = mapped_column(String(500))
    data: Mapped[dict] = mapped_column(JSONB, default=dict)
    t_source: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))   # thời điểm tại nguồn của bản tin gây cảnh báo
    t_receive: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))  # B1 nhận bản tin đó
    raised_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=text("clock_timestamp()"))
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=text("clock_timestamp()"))
    count: Mapped[int] = mapped_column(Integer, default=1)
    acked_by: Mapped[uuid.UUID | None] = mapped_column()
    acked_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by: Mapped[str | None] = mapped_column(String(80))  # "auto" hoặc mã người dùng


class MonitorEvent(Base):
    __tablename__ = "event"
    __table_args__ = {"schema": "monitoring"}
    seq: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=text("clock_timestamp()"))
    kind: Mapped[str] = mapped_column(String(30))  # alert.raised | alert.escalated | alert.acknowledged | alert.resolved
    alert_id: Mapped[uuid.UUID | None] = mapped_column()
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
