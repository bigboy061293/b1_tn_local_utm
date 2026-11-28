"""Giao diện của dịch vụ chuyến bay cho dịch vụ khác (xếp lịch tạo, huỷ chuyến)."""
from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from b1.operations.models import DutyShift, Flight


class OperationsService:
    def __init__(self, sm: sessionmaker[Session]):
        self.sm = sm

    def create_flight(self, *, request_ref: str, uas_id: str, customer_id: uuid.UUID, starts_at: dt.datetime,
                      ends_at: dt.datetime, block_ids: list[str], bvlos: bool) -> uuid.UUID:
        with self.sm() as s:
            f = Flight(request_ref=request_ref, uas_id=uas_id, customer_id=customer_id, starts_at=starts_at,
                       ends_at=ends_at, block_ids=block_ids, bvlos=bvlos)
            s.add(f)
            s.commit()
            return f.id

    def cancel_flight(self, flight_id: uuid.UUID) -> str:
        with self.sm() as s:
            f = s.get(Flight, flight_id, with_for_update=True)
            if f is None:
                return "missing"
            if f.state == "scheduled":
                f.state = "cancelled"
                f.version += 1
                s.commit()
            return f.state

    def flights_between(self, start: dt.datetime, end: dt.datetime, states: tuple[str, ...] = ("scheduled",)) -> list[dict]:
        with self.sm() as s:
            q = select(Flight).where(Flight.starts_at < end, Flight.ends_at > start, Flight.state.in_(states)).order_by(Flight.starts_at)
            return [{"flight_id": str(f.id), "queue_no": f.request_ref, "uas_id": f.uas_id, "customer_id": str(f.customer_id),
                     "starts_at": f.starts_at.isoformat(), "ends_at": f.ends_at.isoformat(), "block_ids": f.block_ids or [],
                     "bvlos": bool(f.bvlos), "state": f.state} for f in s.scalars(q)]

    def busy_in_block(self, block_id: str, start: dt.datetime, end: dt.datetime) -> list[uuid.UUID]:
        """Chuyến đã cho phép cất cánh hoặc đang bay trong khối và khung giờ (không được đóng khối khi còn chuyến này)."""
        with self.sm() as s:
            q = select(Flight).where(Flight.starts_at < end, Flight.ends_at > start, Flight.state.in_(("cleared", "airborne")))
            return [f.id for f in s.scalars(q) if block_id in (f.block_ids or [])]

    def on_duty(self, user_id: uuid.UUID, role: str, at: dt.datetime) -> bool:
        with self.sm() as s:
            return s.scalar(select(DutyShift.id).where(DutyShift.user_id == user_id, DutyShift.role == role,
                                                      DutyShift.cancelled.is_(False), DutyShift.starts_at <= at,
                                                      DutyShift.ends_at > at).limit(1)) is not None
