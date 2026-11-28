from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from b1.db import Base


class Flight(Base):
    """Chuyến bay đã xếp lịch. Bước 2 chỉ có phần cho phép cất cánh (AT-04); danh mục kiểm tra và ký điện tử ở bước 3."""
    __tablename__ = "flight"
    __table_args__ = {"schema": "operations"}
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    request_ref: Mapped[str | None] = mapped_column(String(40))   # số thứ tự HL-YYYYMMDD-NNNN của yêu cầu bay
    uas_id: Mapped[str | None] = mapped_column(String(40))
    customer_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    starts_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    block_ids: Mapped[list | None] = mapped_column(JSONB)
    bvlos: Mapped[bool | None] = mapped_column()
    # scheduled | cleared | airborne | landed | closed | cancelled
    state: Mapped[str] = mapped_column(String(20), default="scheduled")
    takeoff_cleared_by: Mapped[uuid.UUID | None] = mapped_column()
    takeoff_cleared_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DutyShift(Base):
    """Ca trực. Trưởng bộ phận chỉ cho phép cất cánh, ra lệnh dừng khi đang trực (Đặc tả Mục 1.1)."""
    __tablename__ = "duty_shift"
    __table_args__ = {"schema": "operations"}
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(index=True)
    role: Mapped[str] = mapped_column(String(40))
    starts_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    cancelled: Mapped[bool] = mapped_column(default=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column()
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Clearance(Base):
    """Bản ghi cho phép cất cánh đã ký (F-04): danh mục kiểm tra, điều kiện B1 tự kiểm, chữ ký JWS của B1."""
    __tablename__ = "clearance"
    __table_args__ = {"schema": "operations"}
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    flight_id: Mapped[uuid.UUID] = mapped_column(index=True, unique=True)
    record: Mapped[dict] = mapped_column(JSONB)       # nội dung đã ký
    record_sha256: Mapped[str] = mapped_column(String(64))
    signature: Mapped[str] = mapped_column(Text)      # JWS dạng gọn, ES256, khoá của B1
    signed_by: Mapped[uuid.UUID] = mapped_column()
    signed_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
