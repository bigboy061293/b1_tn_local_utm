from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from b1.auth import Principal, current_principal, has, require
from b1.db import get_session
from b1.identity.roles import Perm
from b1.registry.api import check_own, dump, my_customer
from b1.scheduling.models import BlockReason, FlightRequest, RequestItem, SchedulingRun
from b1.scheduling.service import ItemIn, RequestError

router = APIRouter(prefix="/v1/scheduling", tags=["Xếp lịch"])


class ItemBody(BaseModel):
    day: dt.date
    slot: int = Field(ge=0)


class RequestBody(BaseModel):
    aircraft_id: uuid.UUID
    pilot_id: uuid.UUID
    priority_group: int = Field(default=3, ge=1, le=4, description="Nhóm ưu tiên theo Phụ lục 05 của Đề án Hành lang")
    bvlos: bool = True
    block_ids: list[str] = Field(min_length=1)
    items: list[ItemBody] = Field(min_length=1)


class ParamBody(BaseModel):
    value: Any


def _svc(request: Request):
    return request.app.state.scheduling


def _out(request: Request, s: Session, r: FlightRequest, missing=None) -> dict:
    items = s.scalars(select(RequestItem).where(RequestItem.request_id == r.id).order_by(RequestItem.day, RequestItem.slot)).all()
    out = dump(r)
    out["items"] = [dump(i) for i in items]
    out["queue_position"] = _svc(request).queue_position(r)
    if missing is not None:
        out["missing"] = [m.as_dict() for m in missing]
    return out


@router.post("/requests", status_code=201)
def submit(body: RequestBody, request: Request, p: Principal = Depends(require(Perm.REQUEST_OWN)),
           s: Session = Depends(get_session)) -> dict:
    c = my_customer(request, p)
    if c is None or c.status != "active":
        raise HTTPException(403, "tài khoản chưa gắn với khách hàng đang hoạt động")
    try:
        r, missing = _svc(request).submit(p.actor(), c.id, aircraft_id=body.aircraft_id, pilot_id=body.pilot_id,
                                          priority_group=body.priority_group, bvlos=body.bvlos, block_ids=body.block_ids,
                                          items=[ItemIn(i.day, i.slot) for i in body.items])
    except RequestError as e:
        raise HTTPException(422, str(e))
    return _out(request, s, r, missing)


@router.get("/requests")
def list_requests(request: Request, status: str | None = None, p: Principal = Depends(require(Perm.REQUEST_OWN, Perm.FLIGHT_READ_ALL)),
                  s: Session = Depends(get_session)) -> dict:
    q = select(FlightRequest)
    if not has(p, Perm.FLIGHT_READ_ALL):
        c = my_customer(request, p)
        if c is None:
            return {"items": []}
        q = q.where(FlightRequest.customer_id == c.id)
    if status:
        q = q.where(FlightRequest.status == status)
    return {"items": [dump(r) for r in s.scalars(q.order_by(FlightRequest.queue_at)).all()]}


def _load(request: Request, p: Principal, s: Session, request_id: uuid.UUID, write: bool = False) -> FlightRequest:
    r = s.get(FlightRequest, request_id)
    if r is None:
        raise HTTPException(404, "không có yêu cầu")
    if not (write and has(p, Perm.SCHEDULE_MANAGE)):
        check_own(request, p, r.customer_id, write=write)
    return r


@router.get("/requests/{request_id}")
def get_request(request_id: uuid.UUID, request: Request, p: Principal = Depends(require(Perm.REQUEST_OWN, Perm.FLIGHT_READ_ALL)),
                s: Session = Depends(get_session)) -> dict:
    r = _load(request, p, s, request_id)
    out = _out(request, s, r)
    out["block_reasons"] = [dump(b) for b in s.scalars(select(BlockReason).where(BlockReason.request_id == r.id)
                                                       .order_by(BlockReason.at)).all()]
    return out


@router.post("/requests/{request_id}/recheck")
def recheck(request_id: uuid.UUID, request: Request, p: Principal = Depends(require(Perm.REQUEST_OWN)),
            s: Session = Depends(get_session)) -> dict:
    _load(request, p, s, request_id, write=True)
    r, missing = _svc(request).recheck(p.actor(), request_id)
    s.expire_all()
    return _out(request, s, s.get(FlightRequest, r.id), missing)


@router.post("/requests/{request_id}/cancel")
def cancel(request_id: uuid.UUID, request: Request, p: Principal = Depends(require(Perm.REQUEST_OWN, Perm.SCHEDULE_MANAGE)),
           s: Session = Depends(get_session)) -> dict:
    r = _load(request, p, s, request_id, write=True)
    if r.status in ("cancelled", "expired"):
        raise HTTPException(409, f"yêu cầu đang ở trạng thái {r.status}")
    _svc(request).cancel(p.actor(), request_id)
    s.expire_all()
    return _out(request, s, s.get(FlightRequest, request_id))


@router.get("/availability")
def availability(start: dt.date, end: dt.date, request: Request, p: Principal = Depends(current_principal)) -> dict:
    """Công khai cho mọi tài khoản: khung còn trống, số khung đang chờ (Mục 4.3)."""
    if (end - start).days > 62 or end < start:
        raise HTTPException(422, "khoảng tra tối đa 62 ngày")
    return {"items": _svc(request).availability(start, end)}


@router.get("/queue")
def public_queue(request: Request, p: Principal = Depends(current_principal), s: Session = Depends(get_session)) -> dict:
    """Công khai số thứ tự và vị trí trong hàng chờ, không lộ tên khách hàng."""
    waiting = s.scalars(select(FlightRequest).where(FlightRequest.status.in_(("queued", "partial")))).all()
    rows = sorted(({"queue_no": r.queue_no, "queue_at": r.queue_at, "priority_group": r.priority_group,
                    "position": _svc(request).queue_position(r)} for r in waiting), key=lambda x: x["position"])
    return {"items": rows}


@router.post("/runs", status_code=201)
def run(request: Request, as_of: dt.datetime | None = None, p: Principal = Depends(require(Perm.SCHEDULE_MANAGE))) -> dict:
    """Chạy kỳ xếp lịch ngay (thường chạy tự động 16:00 bằng `python -m b1.scheduling.cron`)."""
    r = _svc(request).run(p.actor(), as_of)
    return dump(r)


@router.get("/runs/{run_id}")
def get_run(run_id: uuid.UUID, p: Principal = Depends(require(Perm.SCHEDULE_MANAGE, Perm.FLIGHT_READ_ALL)),
            s: Session = Depends(get_session)) -> dict:
    r = s.get(SchedulingRun, run_id)
    if r is None:
        raise HTTPException(404, "không có")
    return dump(r)


@router.get("/params")
def get_params(request: Request, p: Principal = Depends(current_principal)) -> dict:
    return _svc(request).params().__dict__


@router.put("/params/{key}")
def set_param(key: str, body: ParamBody, request: Request, p: Principal = Depends(require(Perm.SCHEDULE_MANAGE))) -> dict:
    try:
        return _svc(request).set_param(p.actor(), key, body.value).__dict__
    except RequestError as e:
        raise HTTPException(422, str(e))
