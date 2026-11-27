from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from b1.db import Base


class Flight(Base):
    """Chuyến bay đã xếp lịch. Bước 2 chỉ có phần cho phép cất cánh (AT-04); danh mục kiểm tra và ký điện tử ở bước 3."""
    __tablename__ = "flight"
    __table_args__ = {"schema": "operations"}
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    request_ref: Mapped[str | None] = mapped_column(String(40))   # số thứ tự HL-YYYYMMDD-NNNN của yêu cầu bay
    uas_id: Mapped[str | None] = mapped_column(String(40))
    state: Mapped[str] = mapped_column(String(20), default="scheduled")  # scheduled | cleared | airborne | landed | closed
    takeoff_cleared_by: Mapped[uuid.UUID | None] = mapped_column()
    takeoff_cleared_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
