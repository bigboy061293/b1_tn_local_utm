from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import DateTime, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from b1.db import Base


class Notification(Base):
    """Thông báo cho khách hàng: kết quả xếp lịch, lý do chặn, nhắc việc T − 7/T − 3/T − 1.
    Hiện trên cổng khách hàng; kênh thư điện tử, tin nhắn thêm sau qua mô-đun kết nối."""
    __tablename__ = "notification"
    __table_args__ = (UniqueConstraint("dedup_key"), {"schema": "notify"})
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    customer_id: Mapped[uuid.UUID] = mapped_column(index=True)
    kind: Mapped[str] = mapped_column(String(40))
    ref: Mapped[str | None] = mapped_column(String(100))  # số thứ tự hoặc mã khung
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(String(2000))
    dedup_key: Mapped[str] = mapped_column(String(200))  # tránh gửi trùng (ví dụ cùng một lần nhắc)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    read_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
