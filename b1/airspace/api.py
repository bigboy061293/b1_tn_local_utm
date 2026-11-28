"""API vùng trời (F-03, AT-03)."""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.orm import Session

from b1.airspace import package as pkg
from b1.airspace.service import AirspaceError, AirspaceService, activation_out
from b1.auth import Principal, require
from b1.db import get_session
from b1.identity.roles import Perm

router = APIRouter(prefix="/v1/airspace", tags=["Vùng trời"])
Ref = Literal["AGL", "AMSL", "WGS84"]


def svc(request: Request) -> AirspaceService:
    return request.app.state.airspace


def _err(e: AirspaceError) -> HTTPException:
    return HTTPException(e.status, e.detail)


def _flights_for(request: Request, block_id: str, start: dt.datetime, end: dt.datetime) -> list[dict]:
    ops, reg = request.app.state.operations, request.app.state.registry
    out = []
    for f in ops.flights_between(start, end):
        if block_id in f["block_ids"]:
            out.append(_card(reg, f))
    return out


def _card(reg, f: dict) -> dict:
    c = reg.flight_card(uuid.UUID(f["customer_id"]) if f["customer_id"] not in (None, "None") else None, f["uas_id"])
    return {"flight_id": f["flight_id"], "queue_no": f["queue_no"], "uas_id": f["uas_id"], "bvlos": f["bvlos"],
            "starts_at": f["starts_at"], "ends_at": f["ends_at"], "block_ids": f["block_ids"], **c}


# ---------- khối, ràng buộc, trạng thái ----------

@router.get("/blocks")
def list_blocks(request: Request, p: Principal = Depends(require(Perm.AIRSPACE_READ)), s: Session = Depends(get_session)) -> dict:
    a = svc(request)
    return {"items": [{"id": b.id, "name": b.name, "km_start": b.km_start, "km_end": b.km_end, "geometry": b.geometry,
                       "floor_m": b.floor_m, "floor_ref": b.floor_ref, "ceiling_m": b.ceiling_m, "ceiling_ref": b.ceiling_ref,
                       "envelope": a.envelope(s, b), "data_status": b.data_status, "source": b.source} for b in a.blocks(s)]}


class VerticalIn(BaseModel):
    floor_m: float = Field(ge=0)
    floor_ref: Ref = "AGL"
    ceiling_m: float = Field(gt=0)
    ceiling_ref: Ref = "AGL"


@router.patch("/blocks/{block_id}/vertical")
def set_vertical(block_id: str, body: VerticalIn, request: Request, p: Principal = Depends(require(Perm.AIRSPACE_MANAGE))) -> dict:
    """Tầng độ cao khai thác của khối (D3 chưa chốt; mặc định 20–150 m so với mặt đất). Áp cho kế hoạch lập sau đó."""
    try:
        b = svc(request).set_block_vertical(p.actor(), block_id, body.floor_m, body.floor_ref, body.ceiling_m, body.ceiling_ref)
    except AirspaceError as e:
        raise _err(e)
    return {"id": b.id, "floor_m": b.floor_m, "floor_ref": b.floor_ref, "ceiling_m": b.ceiling_m, "ceiling_ref": b.ceiling_ref}


@router.get("/constraints")
def list_constraints(request: Request, p: Principal = Depends(require(Perm.AIRSPACE_READ)), s: Session = Depends(get_session)) -> dict:
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "id": c.id, "geometry": c.geometry, "properties": {"kind": c.kind, "name": c.name, **c.properties}}
        for c in svc(request).constraints(s)]}


@router.get("/state")
def state(request: Request, p: Principal = Depends(require(Perm.AIRSPACE_READ))) -> dict:
    a = svc(request)
    return {"as_of": dt.datetime.now(dt.timezone.utc), "last_seq": a.last_seq(), "blocks": a.state()}


@router.get("/geofence")
def geofence(request: Request, p: Principal = Depends(require(Perm.AIRSPACE_READ))) -> dict:
    return svc(request).geofence()


# ---------- kế hoạch kích hoạt ----------

class ActivationIn(BaseModel):
    block_id: str
    starts_at: dt.datetime
    ends_at: dt.datetime

    @model_validator(mode="after")
    def _tz(self):
        if self.starts_at.tzinfo is None or self.ends_at.tzinfo is None:
            raise ValueError("giờ phải có múi giờ (RFC 3339)")
        return self


@router.post("/activations", status_code=201)
def create_activation(body: ActivationIn, request: Request, p: Principal = Depends(require(Perm.AIRSPACE_MANAGE))) -> dict:
    flights = _flights_for(request, body.block_id, body.starts_at, body.ends_at)
    try:
        a = svc(request).create_activation(p.actor(), body.block_id, body.starts_at, body.ends_at, flights)
    except AirspaceError as e:
        raise _err(e)
    return activation_out(a)


class PlanIn(BaseModel):
    day: dt.date


@router.post("/activations/plan")
def plan_day(body: PlanIn, request: Request, p: Principal = Depends(require(Perm.AIRSPACE_MANAGE))) -> dict:
    """Lập kế hoạch kích hoạt cho mọi chuyến đã xếp lịch trong ngày (giờ Việt Nam), mỗi khối mỗi khung giờ một kế hoạch."""
    start = dt.datetime.combine(body.day, dt.time(0), tzinfo=pkg.TZ)
    flights = [_card(request.app.state.registry, f)
               for f in request.app.state.operations.flights_between(start, start + dt.timedelta(days=1))]
    created, skipped = svc(request).plan_from_flights(p.actor(), flights)
    return {"created": [activation_out(a) for a in created], "skipped": skipped}


@router.get("/activations")
def list_activations(request: Request, start: dt.datetime | None = None, end: dt.datetime | None = None,
                     block_id: str | None = None, state: str | None = None,
                     p: Principal = Depends(require(Perm.FLIGHT_READ_ALL)), s: Session = Depends(get_session)) -> dict:
    return {"items": [activation_out(a) for a in svc(request).list_activations(s, start, end, block_id, state)]}


@router.get("/activations/{activation_id}")
def get_activation(activation_id: uuid.UUID, request: Request, p: Principal = Depends(require(Perm.FLIGHT_READ_ALL)),
                   s: Session = Depends(get_session)) -> dict:
    try:
        return activation_out(svc(request).get(s, activation_id))
    except AirspaceError as e:
        raise _err(e)


@router.get("/activations/{activation_id}/package")
def get_package(activation_id: uuid.UUID, request: Request, format: Literal["text", "geojson", "ed269", "json"] = "text",
                p: Principal = Depends(require(Perm.FLIGHT_READ_ALL))):
    """Gói dữ liệu kích hoạt: văn bản cho công văn, GeoJSON, ED-269, hoặc nội dung chuẩn (json). Mã băm ở X-Package-SHA256."""
    try:
        content = svc(request).package(activation_id)
    except AirspaceError as e:
        raise _err(e)
    digest = pkg.sha256(content)
    headers = {"X-Package-SHA256": digest}
    request.app.state.audit.record(p.actor(), "airspace.package.export", target_type="activation", target_id=activation_id,
                                   detail={"format": format, "sha256": digest})
    if format == "text":
        return PlainTextResponse(pkg.as_text(content), headers=headers)
    body = content if format == "json" else pkg.FORMATS[format](content)
    media = "application/geo+json" if format == "geojson" else "application/json"
    return JSONResponse(json.loads(json.dumps(body, default=str)), headers=headers, media_type=media)


class CoordinationIn(BaseModel):
    result: Literal["approved", "rejected"]
    package_sha256: str = Field(min_length=64, max_length=64, description="Mã băm của gói đã gửi đi hiệp đồng")
    reference: str = Field(min_length=1, max_length=200, description="Số công văn hoặc số điện hiệp đồng")
    counterpart: str = Field(min_length=1, max_length=200, description="Cơ quan, người hiệp đồng")
    note: str | None = Field(default=None, max_length=2000)


@router.post("/activations/{activation_id}/coordination")
def coordinate(activation_id: uuid.UUID, body: CoordinationIn, request: Request,
               p: Principal = Depends(require(Perm.AIRSPACE_MANAGE, target_type="activation", target_param="activation_id"))) -> dict:
    try:
        a = svc(request).coordinate(p.actor(), activation_id, result=body.result, package_sha256=body.package_sha256,
                                    reference=body.reference, counterpart=body.counterpart, note=body.note)
    except AirspaceError as e:
        raise _err(e)
    return activation_out(a)


@router.post("/activations/{activation_id}/activate")
def activate(activation_id: uuid.UUID, request: Request,
             p: Principal = Depends(require(Perm.AIRSPACE_MANAGE, target_type="activation", target_param="activation_id"))) -> dict:
    try:
        return activation_out(svc(request).activate(p.actor(), activation_id))
    except AirspaceError as e:
        raise _err(e)


@router.post("/activations/{activation_id}/close")
def close(activation_id: uuid.UUID, request: Request,
          p: Principal = Depends(require(Perm.AIRSPACE_MANAGE, target_type="activation", target_param="activation_id"))) -> dict:
    try:
        return activation_out(svc(request).close(p.actor(), activation_id, request.app.state.operations.busy_in_block))
    except AirspaceError as e:
        raise _err(e)


@router.post("/activations/{activation_id}/cancel")
def cancel(activation_id: uuid.UUID, request: Request,
           p: Principal = Depends(require(Perm.AIRSPACE_MANAGE, target_type="activation", target_param="activation_id"))) -> dict:
    try:
        return activation_out(svc(request).close(p.actor(), activation_id, lambda *a: [], cancel=True))
    except AirspaceError as e:
        raise _err(e)


# ---------- sự kiện ----------

@router.get("/events")
def events(request: Request, after: int = 0, limit: int = Query(200, le=1000),
           p: Principal = Depends(require(Perm.AIRSPACE_READ))) -> dict:
    return {"items": svc(request).events_after(after, limit)}


@router.get("/stream")
async def stream(request: Request, after: int | None = None, p: Principal = Depends(require(Perm.AIRSPACE_READ))):
    """Luồng Server-Sent Events của trạng thái vùng trời. Trình duyệt đọc bằng fetch (gửi được tiêu đề Authorization)."""
    a = svc(request)
    poll = request.app.state.settings.airspace_poll_s

    async def gen():
        seq = after if after is not None else await asyncio.to_thread(a.last_seq)
        yield f"event: hello\ndata: {json.dumps({'seq': seq})}\n\n"
        idle = 0.0
        while not await request.is_disconnected():
            items = await asyncio.to_thread(a.events_after, seq)
            for e in items:
                seq = e["seq"]
                yield f"id: {seq}\nevent: airspace\ndata: {json.dumps(e, ensure_ascii=False, default=str)}\n\n"
            idle = 0.0 if items else idle + poll
            if idle >= 15:
                idle = 0.0
                yield ": keep-alive\n\n"
            await asyncio.sleep(poll)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
