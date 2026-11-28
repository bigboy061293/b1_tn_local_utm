"""Phiên kiểm tra tuân thủ dữ liệu CT-1…8: khách tự kiểm, Kỹ thuật viên bay thử chạy chính thức."""
from __future__ import annotations

import datetime as dt
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from b1.auth import Principal, has, require
from b1.identity.roles import Perm
from b1.telemetry.ct import ALL_TESTS, MARKS, CtError, CtService, session_out

router = APIRouter(prefix="/v1/ct-sessions", tags=["Kiểm tra tuân thủ CT-1…8"])


def svc(request: Request) -> CtService:
    return request.app.state.ctsessions


class RtkRef(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


class SessionIn(BaseModel):
    aircraft_id: uuid.UUID
    tests: list[Literal[ALL_TESTS]] = list(ALL_TESTS)  # type: ignore[valid-type]
    official: bool = Field(False, description="Chỉ Kỹ thuật viên bay thử: kết quả ghi vào hồ sơ phương tiện")
    rtk_ref: RtkRef | None = Field(None, description="Điểm chuẩn RTK cho CT-4")
    min_duration_s: int = Field(1800, ge=60, le=7200, description="CT-1, CT-2 cần 30 phút; phiên tự kiểm có thể ngắn hơn")
    disconnect_s: int = Field(60, ge=10, le=600)


def _load(request: Request, p: Principal, session_id: uuid.UUID):
    c = svc(request).get(session_id)
    if c is None:
        raise HTTPException(404, "không có phiên")
    if not has(p, Perm.CONFORMANCE_RECORD) and not has(p, Perm.FLIGHT_READ_ALL):
        mine = request.app.state.registry.customer_of_org(p.org_id)
        if mine is None or mine.id != c.customer_id:
            raise HTTPException(404, "không có phiên")
    return c


@router.post("", status_code=201)
def start(body: SessionIn, request: Request, p: Principal = Depends(require(Perm.CONFORMANCE_RECORD, Perm.REQUEST_OWN))) -> dict:
    a = request.app.state.registry.aircraft(body.aircraft_id)
    if a is None:
        raise HTTPException(404, "không có phương tiện")
    tech = has(p, Perm.CONFORMANCE_RECORD)
    if body.official and not tech:
        raise HTTPException(403, "phiên chính thức do Kỹ thuật viên bay thử mở")
    if body.official and body.min_duration_s < 1800:
        raise HTTPException(422, "phiên chính thức cần ít nhất 30 phút dữ liệu (CT-1, CT-2)")
    if not tech:
        mine = request.app.state.registry.customer_of_org(p.org_id)
        if mine is None or mine.id != a["customer_id"]:
            raise HTTPException(403, "phương tiện không thuộc khách hàng này")
    params = {"min_duration_s": body.min_duration_s, "disconnect_s": body.disconnect_s,
              "rtk_ref": body.rtk_ref.model_dump() if body.rtk_ref else None}
    return session_out(svc(request).start(p.actor(), a, official=body.official, tests=list(body.tests), params=params))


@router.get("/{session_id}")
def get(session_id: uuid.UUID, request: Request, p: Principal = Depends(require(Perm.CONFORMANCE_RECORD, Perm.REQUEST_OWN,
                                                                               Perm.FLIGHT_READ_ALL))) -> dict:
    return session_out(_load(request, p, session_id))


class MarkIn(BaseModel):
    kind: Literal[MARKS]  # type: ignore[valid-type]
    at: dt.datetime | None = Field(None, description="Mặc định là giờ B1 nhận yêu cầu")


@router.post("/{session_id}/marks")
def mark(session_id: uuid.UUID, body: MarkIn, request: Request,
         p: Principal = Depends(require(Perm.CONFORMANCE_RECORD, Perm.REQUEST_OWN))) -> dict:
    """Đánh dấu đoạn thử: đứng yên tại điểm RTK (CT-4), chạy tải (CT-7), ngắt kết nối (CT-8)."""
    _load(request, p, session_id)
    try:
        return session_out(svc(request).mark(p.actor(), session_id, body.kind, body.at))
    except CtError as e:
        raise HTTPException(e.status, e.detail)


@router.post("/{session_id}/evaluate")
def evaluate(session_id: uuid.UUID, request: Request, p: Principal = Depends(require(Perm.CONFORMANCE_RECORD, Perm.REQUEST_OWN))) -> dict:
    c = _load(request, p, session_id)
    if c.official and not has(p, Perm.CONFORMANCE_RECORD):
        raise HTTPException(403, "phiên chính thức do Kỹ thuật viên bay thử chấm")
    try:
        return session_out(svc(request).evaluate(p.actor(), session_id, record=c.official))
    except CtError as e:
        raise HTTPException(e.status, e.detail)
