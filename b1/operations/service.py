"""Giao diện của dịch vụ chuyến bay cho dịch vụ khác (xếp lịch tạo, huỷ chuyến)."""
from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy.orm import Session, sessionmaker

from b1.operations.models import Flight


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
