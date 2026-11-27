"""Xếp lịch (F-02)."""
from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from b1.db import Base

S = {"schema": "scheduling"}


class FlightRequest(Base):
    __tablename__ = "flight_request"
    __table_args__ = S
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    queue_no: Mapped[str] = mapped_column(String(32), unique=True)  # HL-YYYYMMDD-NNNN hoặc số tạm TAM-…
    queue_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))  # thời điểm đến, tới từng giây
    provisional: Mapped[bool] = mapped_column(Boolean)
    hold_until: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    complete_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))  # hồ sơ đầy đủ: mốc 3 ngày làm việc
    customer_id: Mapped[uuid.UUID] = mapped_column(index=True)
    customer_tier: Mapped[str] = mapped_column(String(1))
    aircraft_id: Mapped[uuid.UUID] = mapped_column()
    uas_id: Mapped[str] = mapped_column(String(40))
    pilot_id: Mapped[uuid.UUID] = mapped_column()
    priority_group: Mapped[int] = mapped_column(Integer)  # 1–4 theo Phụ lục 05 của Đề án Hành lang
    rebook_right_until: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    bvlos: Mapped[bool] = mapped_column(Boolean, default=True)
    block_ids: Mapped[list] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(24))  # provisional | queued | scheduled | partial | not_scheduled | expired | cancelled
    created_by: Mapped[uuid.UUID | None] = mapped_column()  # None = hệ thống
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class RequestItem(Base):
    """Một khung bay trong yêu cầu (một ngày, một khung)."""
    __tablename__ = "request_item"
    __table_args__ = S
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    request_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("scheduling.flight_request.id"), index=True)
    day: Mapped[dt.date] = mapped_column(Date, index=True)
    slot: Mapped[int] = mapped_column(Integer)
    starts_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="requested")  # requested | scheduled | not_scheduled | cancelled
    last_reason: Mapped[str | None] = mapped_column(String(40))
    flight_id: Mapped[uuid.UUID | None] = mapped_column()
    decided_in_run: Mapped[uuid.UUID | None] = mapped_column()
    final_checked_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))  # xác nhận lần cuối T − 3


class BlockReason(Base):
    """Mọi lần chặn hoặc từ chối (AT-01). Song song với nhật ký truy vết, để tra theo yêu cầu bay."""
    __tablename__ = "block_reason"
    __table_args__ = S
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    request_id: Mapped[uuid.UUID] = mapped_column(index=True)
    item_id: Mapped[uuid.UUID | None] = mapped_column()
    stage: Mapped[str] = mapped_column(String(20))  # submit | recheck | run | final
    rule: Mapped[str] = mapped_column(String(40))
    detail: Mapped[str | None] = mapped_column(String(300))
    run_id: Mapped[uuid.UUID | None] = mapped_column()
    at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SchedulingRun(Base):
    __tablename__ = "scheduling_run"
    __table_args__ = S
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    run_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    kind: Mapped[str] = mapped_column(String(20))  # daily | final_confirmation
    triggered_by: Mapped[str] = mapped_column(String(300))
    inputs_hash: Mapped[str] = mapped_column(String(64))
    decisions: Mapped[list] = mapped_column(JSONB)  # lý do xếp hoặc không xếp từng khung, để giải trình
    summary: Mapped[dict] = mapped_column(JSONB)


class QueueCounter(Base):
    __tablename__ = "queue_counter"
    __table_args__ = S
    key: Mapped[str] = mapped_column(String(20), primary_key=True)  # HL-YYYYMMDD hoặc TAM-YYYYMMDD
    last: Mapped[int] = mapped_column(Integer, default=0)


class Param(Base):
    __tablename__ = "param"
    __table_args__ = S
    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[dict | list | str | int | float] = mapped_column(JSONB)
    effective_from: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    set_by: Mapped[uuid.UUID | None] = mapped_column()
