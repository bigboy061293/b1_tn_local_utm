"""Telemetry của khách hàng (I-07): mẫu đã chuẩn hoá, mẫu bị loại, gán MAVLink, phiên kiểm tra tuân thủ CT-1…8."""
from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import BigInteger, Boolean, DateTime, Float, Index, Integer, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from b1.db import Base


class Sample(Base):
    """Một mẫu telemetry. Ba nhãn thời gian (D6): tại nguồn, B1 nhận, ghi vào CSDL (đồng hồ của máy chủ CSDL).

    Khử trùng theo (flight_id, uas_id, t_source): gửi lại hoặc gửi bù cùng mẫu không tạo bản ghi mới (CT-8).
    """
    __tablename__ = "sample"
    __table_args__ = (UniqueConstraint("flight_id", "uas_id", "t_source", name="sample_dedup"),
                      Index("sample_flight_time", "flight_id", "t_source"), {"schema": "telemetry"})
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    flight_id: Mapped[uuid.UUID] = mapped_column()
    uas_id: Mapped[str] = mapped_column(String(64))
    t_source: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    t_receive: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    t_ingest: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=text("clock_timestamp()"))
    channel: Mapped[str] = mapped_column(String(10))  # https | mavlink | mqtt
    backfill: Mapped[bool] = mapped_column(Boolean, default=False)
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    alt_value: Mapped[float] = mapped_column(Float)
    alt_ref: Mapped[str] = mapped_column(String(16))
    alt_agl: Mapped[float | None] = mapped_column(Float)    # do phương tiện gửi, hoặc suy từ độ cao địa hình (xem config)
    alt_amsl: Mapped[float | None] = mapped_column(Float)   # suy ra để so với trần theo mực nước biển
    ground_speed: Mapped[float] = mapped_column(Float)
    vertical_speed: Mapped[float] = mapped_column(Float)
    track: Mapped[float] = mapped_column(Float)
    fix_type: Mapped[str] = mapped_column(String(10))
    satellites: Mapped[int] = mapped_column(Integer)
    hdop: Mapped[float] = mapped_column(Float)
    battery_pct: Mapped[float | None] = mapped_column(Float)
    voltage: Mapped[float | None] = mapped_column(Float)
    fuel_pct: Mapped[float | None] = mapped_column(Float)
    mode: Mapped[str] = mapped_column(String(40))
    autonomy: Mapped[str] = mapped_column(String(12))
    armed: Mapped[bool] = mapped_column(Boolean)
    safety_flags: Mapped[list] = mapped_column(JSONB, default=list)
    c2_state: Mapped[str] = mapped_column(String(10))
    c2_rssi_dbm: Mapped[float | None] = mapped_column(Float)
    c2_quality_pct: Mapped[float | None] = mapped_column(Float)


class RejectedSample(Base):
    """Mẫu sai lược đồ hoặc sai chuyến: giữ lý do để khách sửa và để chấm CT-1."""
    __tablename__ = "rejected"
    __table_args__ = {"schema": "telemetry"}
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    flight_id: Mapped[uuid.UUID] = mapped_column(index=True)
    channel: Mapped[str] = mapped_column(String(10))
    t_receive: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str] = mapped_column(String(500))
    sample: Mapped[dict | None] = mapped_column(JSONB)


class MavlinkBinding(Base):
    """Gán một hệ thống MAVLink (sysid) vào chuyến. Khoá ký MAVLink (32 byte, hex) nếu khách bật ký bản tin."""
    __tablename__ = "mavlink_binding"
    __table_args__ = {"schema": "telemetry"}
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    sysid: Mapped[int] = mapped_column(Integer, index=True)
    flight_id: Mapped[uuid.UUID] = mapped_column()
    uas_id: Mapped[str] = mapped_column(String(64))
    signing_key_hex: Mapped[str | None] = mapped_column(String(64))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column()
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CtSession(Base):
    """Phiên kiểm tra tuân thủ dữ liệu CT-1…8. Khách gửi telemetry tới `flight_id` của phiên, B1 chấm từng bài.

    `official`: phiên do Kỹ thuật viên bay thử mở, kết quả ghi vào hồ sơ phương tiện; phiên khách tự mở chỉ để tự kiểm.
    """
    __tablename__ = "ct_session"
    __table_args__ = {"schema": "telemetry"}
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)  # cũng là flight_id khi gửi telemetry
    customer_id: Mapped[uuid.UUID] = mapped_column(index=True)
    aircraft_id: Mapped[uuid.UUID] = mapped_column()
    uas_id: Mapped[str] = mapped_column(String(64))
    official: Mapped[bool] = mapped_column(Boolean, default=False)
    tests: Mapped[list] = mapped_column(JSONB)
    params: Mapped[dict] = mapped_column(JSONB, default=dict)   # điểm RTK, cửa sổ tải, cửa sổ ngắt kết nối, TLS
    state: Mapped[str] = mapped_column(String(12), default="open")  # open | evaluated | cancelled
    results: Mapped[dict | None] = mapped_column(JSONB)
    started_by: Mapped[uuid.UUID | None] = mapped_column()
    started_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    evaluated_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    note: Mapped[str | None] = mapped_column(Text)
