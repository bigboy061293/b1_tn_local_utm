from __future__ import annotations

import datetime as dt
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from b1.auth import Principal, require
from b1.db import get_session
from b1.identity.roles import Perm
from b1.operations.models import Flight

router = APIRouter(prefix="/v1/flights", tags=["Chuyến bay"])


class FlightIn(BaseModel):
    request_ref: str | None = None
    uas_id: str | None = None


def _out(f: Flight) -> dict:
    return {"id": str(f.id), "request_ref": f.request_ref, "uas_id": f.uas_id, "state": f.state,
            "takeoff_cleared_by": f.takeoff_cleared_by and str(f.takeoff_cleared_by),
            "takeoff_cleared_at": f.takeoff_cleared_at, "version": f.version}


@router.post("", status_code=201)
def create_flight(body: FlightIn, request: Request, p: Principal = Depends(require(Perm.SCHEDULE_MANAGE)),
                  s: Session = Depends(get_session)) -> dict:
    """Tạm thời: bước 2c sẽ tạo chuyến từ yêu cầu bay đã được xếp lịch."""
    f = Flight(request_ref=body.request_ref, uas_id=body.uas_id)
    s.add(f)
    s.commit()
    request.app.state.audit.record(p.actor(), "flight.create", target_type="flight", target_id=f.id, after=_out(f))
    return _out(f)


@router.get("/{flight_id}")
def get_flight(flight_id: uuid.UUID, p: Principal = Depends(require(Perm.FLIGHT_READ_ALL)), s: Session = Depends(get_session)) -> dict:
    f = s.get(Flight, flight_id)
    if not f:
        raise HTTPException(404, "không có chuyến bay")
    return _out(f)


@router.post("/{flight_id}/takeoff-clearance")
def clear_takeoff(flight_id: uuid.UUID, request: Request,
                  p: Principal = Depends(require(Perm.FLIGHT_CLEAR_TAKEOFF, "flight", "flight_id")),
                  s: Session = Depends(get_session)) -> dict:
    """F-04: chỉ Cán bộ an toàn bay trực ban và Trưởng bộ phận, có MFA. Không phải phép bay (Đặc tả Mục 1)."""
    f = s.get(Flight, flight_id, with_for_update=True)
    if not f:
        raise HTTPException(404, "không có chuyến bay")
    before = _out(f)
    if f.state != "scheduled":
        request.app.state.audit.record(p.actor(), Perm.FLIGHT_CLEAR_TAKEOFF.value, "rejected", target_type="flight",
                                       target_id=f.id, before=before, detail={"reason": f"trạng thái {f.state}"})
        raise HTTPException(409, f"chuyến bay đang ở trạng thái {f.state}")
    f.state, f.takeoff_cleared_by, f.takeoff_cleared_at = "cleared", p.user_id, dt.datetime.now(dt.timezone.utc)
    f.version += 1
    s.commit()
    request.app.state.audit.record(p.actor(), Perm.FLIGHT_CLEAR_TAKEOFF.value, target_type="flight", target_id=f.id,
                                   before=before, after=_out(f))
    return _out(f)
