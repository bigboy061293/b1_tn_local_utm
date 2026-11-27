"""Đăng ký và hồ sơ (F-01): khách hàng, người điều khiển, phương tiện bay, giấy tờ, kết quả CT-1…8."""
from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from b1.db import Base

S = {"schema": "registry"}


class _Stamp:
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Customer(_Stamp, Base):
    __tablename__ = "customer"
    __table_args__ = S
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(unique=True)  # tổ chức trong dịch vụ danh tính (tham chiếu, không khoá ngoại chéo schema)
    name: Mapped[str] = mapped_column(String(200))
    unified_id: Mapped[str | None] = mapped_column(String(64), unique=True)  # mã định danh thống nhất (I-08), dùng cho I-01
    tier: Mapped[str] = mapped_column(String(1), default="C")  # hạng A/B/C, dùng khi xét trùng khung (Mục 4.3)
    kind: Mapped[str] = mapped_column(String(20), default="operator")  # operator | training
    status: Mapped[str] = mapped_column(String(20), default="active")


class Pilot(_Stamp, Base):
    __tablename__ = "pilot"
    __table_args__ = S
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("registry.customer.id"), index=True)
    full_name: Mapped[str] = mapped_column(String(200))
    unified_id: Mapped[str | None] = mapped_column(String(64))  # mã định danh thống nhất (dữ liệu cá nhân)


class Aircraft(_Stamp, Base):
    __tablename__ = "aircraft"
    __table_args__ = S
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("registry.customer.id"), index=True)
    uas_id: Mapped[str] = mapped_column(String(40), unique=True)  # số sê-ri ANSI/CTA-2063-A
    vn_registration_no: Mapped[str | None] = mapped_column(String(40), unique=True)  # Thông tư 78/2026/TT-BCA
    model: Mapped[str | None] = mapped_column(String(100))
    mtow_kg: Mapped[float] = mapped_column(Float)
    max_speed_ms: Mapped[float] = mapped_column(Float)
    in_testing: Mapped[bool] = mapped_column(Boolean, default=True)  # đang thử nghiệm: cần Giấy xác nhận tình trạng kỹ thuật
    flight_mgmt_device_id: Mapped[str | None] = mapped_column(String(64))  # thiết bị quản lý hoạt động bay
    flight_mgmt_device_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    # Phiên bản phần mềm bay tự động và cấu hình vô tuyến khách khai. Đổi giá trị này làm mất hiệu lực CT-1…8.
    config_ref: Mapped[str] = mapped_column(String(100), default="")
    weather_limits: Mapped[dict | None] = mapped_column(JSONB)


DOC_KINDS = ("registration_certificate", "temporary_registration", "technical_status_certificate", "pilot_licence",
             "test_permit", "flight_permit", "insurance")


class Document(_Stamp, Base):
    __tablename__ = "document"
    __table_args__ = S
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("registry.customer.id"), index=True)
    owner_type: Mapped[str] = mapped_column(String(20))  # aircraft | pilot | customer
    owner_id: Mapped[uuid.UUID] = mapped_column(index=True)
    kind: Mapped[str] = mapped_column(String(40))
    number: Mapped[str] = mapped_column(String(80))
    valid_from: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    valid_to: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    file_ref: Mapped[str | None] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(20), default="submitted")  # submitted | verified | rejected
    verified_by: Mapped[uuid.UUID | None] = mapped_column()
    verified_via: Mapped[str | None] = mapped_column(String(20))  # manual | I-02
    verified_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    note: Mapped[str | None] = mapped_column(String(500))


CT_TESTS = tuple(f"CT-{i}" for i in range(1, 9))


class ConformanceResult(_Stamp, Base):
    __tablename__ = "conformance_result"
    __table_args__ = S
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    aircraft_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("registry.aircraft.id"), index=True)
    test: Mapped[str] = mapped_column(String(8))
    passed: Mapped[bool] = mapped_column(Boolean)
    measured: Mapped[dict | None] = mapped_column(JSONB)
    report_ref: Mapped[str | None] = mapped_column(String(300))
    tested_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    valid_until: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))  # 12 tháng
    config_ref: Mapped[str] = mapped_column(String(100))  # cấu hình phương tiện lúc thử
    recorded_by: Mapped[uuid.UUID | None] = mapped_column()
