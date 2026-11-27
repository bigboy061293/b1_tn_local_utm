from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from b1.db import Base


class Organization(Base):
    __tablename__ = "organization"
    __table_args__ = {"schema": "identity"}
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    kind: Mapped[str] = mapped_column(String(20))  # corridor | customer | regulator | auditor
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UserAccount(Base):
    """Một người, một tài khoản (cấm tài khoản dùng chung). `subject` là claim `sub` của bên cấp danh tính."""
    __tablename__ = "user_account"
    __table_args__ = (UniqueConstraint("issuer", "subject"), {"schema": "identity"})
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    issuer: Mapped[str] = mapped_column(String(300))
    subject: Mapped[str] = mapped_column(String(300))
    display_name: Mapped[str] = mapped_column(String(200))
    org_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("identity.organization.id"))
    status: Mapped[str] = mapped_column(String(20), default="active")  # active | disabled
    expires_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))  # tài khoản khách hết hạn theo đợt thử
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class RoleAssignment(Base):
    __tablename__ = "role_assignment"
    __table_args__ = {"schema": "identity"}
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("identity.user_account.id"), index=True)
    role: Mapped[str] = mapped_column(String(40))
    area_scope: Mapped[str] = mapped_column(String(40), default="corridor-tn")  # để mở rộng toàn tỉnh
    valid_from: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    valid_to: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    granted_by: Mapped[uuid.UUID | None] = mapped_column()
