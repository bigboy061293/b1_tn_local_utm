"""Vùng trời (F-03): khối, ràng buộc, rào địa lý, kế hoạch kích hoạt khối và luồng sự kiện trạng thái."""
from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import BigInteger, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from b1.db import Base

S = {"schema": "airspace"}


class Block(Base):
    """Khối khai thác. Hình học lấy từ config/corridor/blocks.geojson (dự thảo theo KML v2.6), nạp lại khi khởi động."""
    __tablename__ = "block"
    __table_args__ = S
    id: Mapped[str] = mapped_column(String(20), primary_key=True)  # block-1 … block-4
    name: Mapped[str] = mapped_column(String(80))
    km_start: Mapped[float] = mapped_column(Float)
    km_end: Mapped[float] = mapped_column(Float)
    geometry: Mapped[dict] = mapped_column(JSONB)                   # GeoJSON Polygon, WGS84
    # Tầng khai thác (D3 chưa chốt): mặc định theo demo 20–150 m so với mặt đất; trần bao lấy từ ràng buộc.
    floor_m: Mapped[float] = mapped_column(Float, default=20.0)
    floor_ref: Mapped[str] = mapped_column(String(8), default="AGL")
    ceiling_m: Mapped[float] = mapped_column(Float, default=150.0)
    ceiling_ref: Mapped[str] = mapped_column(String(8), default="AGL")
    source: Mapped[str | None] = mapped_column(String(200))
    data_status: Mapped[str] = mapped_column(String(20), default="draft")  # draft | approved
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Constraint(Base):
    """Ràng buộc vùng trời và rào địa lý cứng (Đặc tả Mục 2.2). Hình học có thể rỗng khi đang chờ dữ liệu."""
    __tablename__ = "constraint"
    __table_args__ = S
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    kind: Mapped[str] = mapped_column(String(30))  # airspace_constraint | hard_geofence | reference_layer
    name: Mapped[str] = mapped_column(String(200))
    geometry: Mapped[dict | None] = mapped_column(JSONB)
    properties: Mapped[dict] = mapped_column(JSONB, default=dict)


class Activation(Base):
    """Kế hoạch kích hoạt một khối trong một khung giờ.

    planned → coordinated (đã hiệp đồng, gắn mã băm của gói đã gửi) → active → closed; hoặc cancelled / rejected.
    """
    __tablename__ = "activation"
    __table_args__ = S
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    block_id: Mapped[str] = mapped_column(ForeignKey("airspace.block.id"), index=True)
    starts_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    floor_m: Mapped[float] = mapped_column(Float)
    floor_ref: Mapped[str] = mapped_column(String(8))
    ceiling_m: Mapped[float] = mapped_column(Float)
    ceiling_ref: Mapped[str] = mapped_column(String(8))
    flights: Mapped[list] = mapped_column(JSONB, default=list)  # ảnh chụp thông tin chuyến lúc lập kế hoạch
    state: Mapped[str] = mapped_column(String(20), default="planned")
    package_sha256: Mapped[str | None] = mapped_column(String(64))  # gói đã gửi đi hiệp đồng
    coordination_ref: Mapped[str | None] = mapped_column(String(200))  # số công văn, số điện hiệp đồng
    coordination_counterpart: Mapped[str | None] = mapped_column(String(200))
    coordination_note: Mapped[str | None] = mapped_column(Text)
    coordinated_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    coordinated_by: Mapped[uuid.UUID | None] = mapped_column()
    activated_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    activated_by: Mapped[uuid.UUID | None] = mapped_column()
    closed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    closed_by: Mapped[uuid.UUID | None] = mapped_column()
    created_by: Mapped[uuid.UUID | None] = mapped_column()
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    version: Mapped[int] = mapped_column(Integer, default=1)


class AirspaceEvent(Base):
    """Hộp thư đi của trạng thái vùng trời: bản đồ và dịch vụ khác đọc theo số thứ tự tăng dần (F-03: ≤5 giây)."""
    __tablename__ = "event"
    __table_args__ = S
    seq: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    kind: Mapped[str] = mapped_column(String(40))
    block_id: Mapped[str | None] = mapped_column(String(20))
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
