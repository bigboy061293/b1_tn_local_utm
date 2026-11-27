from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import DateTime, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from b1.db import Base


class Application(Base):
    """Hồ sơ nhận qua I-01 từ cổng đăng ký bay của tỉnh; một hồ sơ sinh một hoặc nhiều yêu cầu bay."""
    __tablename__ = "application"
    __table_args__ = {"schema": "gateway"}
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    external_ref: Mapped[str] = mapped_column(String(100), unique=True)  # mã hồ sơ trên cổng tỉnh: gửi lại không tạo trùng
    client_subject: Mapped[str] = mapped_column(String(300))
    payload: Mapped[dict] = mapped_column(JSONB)
    customer_id: Mapped[uuid.UUID | None] = mapped_column()
    request_ids: Mapped[list] = mapped_column(JSONB, default=list)
    state: Mapped[str] = mapped_column(String(20))  # received | rejected (các trạng thái khác suy ra từ yêu cầu bay)
    reject_reasons: Mapped[list] = mapped_column(JSONB, default=list)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
