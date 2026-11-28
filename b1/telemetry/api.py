"""I-07 Cổng telemetry của khách hàng (hợp đồng api/i07-telemetry.openapi.yaml) và đọc telemetry cho vận hành.

Xác thực: token OAuth 2.0 của khách (tài khoản máy kiểu client credentials hoặc người dùng của khách) có phạm vi
`telemetry:write`; chỉ gửi được cho chuyến của chính khách, đã được cho phép cất cánh. TLS 1.3 và chứng chỉ hai chiều do
cổng vào (ingress) đảm nhận, B1 đọc kết quả qua tiêu đề do cổng vào gắn (CT-6).
"""
from __future__ import annotations

import datetime as dt
import math
import uuid

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from b1.auth import Principal, has, require
from b1.db import get_session
from b1.identity.roles import Perm
from b1.telemetry import timeline
from b1.telemetry.models import MavlinkBinding
from b1.telemetry.service import IngestError, TelemetryService, iso_ms

router = APIRouter(prefix="/telemetry/v1", tags=["I-07 Telemetry"])
ops_router = APIRouter(prefix="/v1/flights", tags=["Telemetry (vận hành)"])

SCOPE = "telemetry:write"


def svc(request: Request) -> TelemetryService:
    return request.app.state.telemetry


def customer_of(request: Request, p: Principal) -> uuid.UUID:
    c = request.app.state.registry.customer_of_org(p.org_id)
    if c is None:
        raise HTTPException(403, "tài khoản chưa gắn với khách hàng")
    return c.id


def _problem(e: IngestError) -> HTTPException:
    headers = {"Retry-After": str(max(1, math.ceil(e.retry_after)))} if e.retry_after is not None else None
    return HTTPException(e.status, e.detail, headers=headers)


def tls_info(request: Request) -> dict:
    """Thông tin TLS do cổng vào gắn (CT-6). Không có tiêu đề thì là kết nối nội bộ hoặc kiểm thử."""
    h = request.headers
    return {"tls_version": h.get("x-b1-tls-version"), "client_cert_verified": h.get("x-b1-client-verify"),
            "client_cert_subject": h.get("x-b1-client-subject")}


def _ingest(request: Request, flight_id: uuid.UUID, body: dict, p: Principal, backfill: bool) -> JSONResponse:
    t_receive = dt.datetime.now(dt.timezone.utc)
    if request.app.state.settings.telemetry_require_scope and SCOPE not in str(p.claims.get("scope", "")).split():
        raise HTTPException(403, f"token thiếu phạm vi quyền {SCOPE}")
    samples = body.get("samples") if isinstance(body, dict) else None
    if not isinstance(samples, list) or not 1 <= len(samples) <= 200:
        raise HTTPException(400, "thân yêu cầu phải là {\"samples\": [...]} với 1–200 mẫu")
    t = svc(request)
    try:
        target = t.check_target(t.target(flight_id), customer_of(request, p), backfill)
        ack = t.ingest(target, samples, "https", backfill=backfill, t_receive=t_receive)
    except IngestError as e:
        raise _problem(e)
    if target.ct_session:
        request.app.state.ctsessions.note_transport(target.id, tls_info(request))
    ack.pop("alerts", None)
    return JSONResponse(ack, status_code=202)


@router.post("/flights/{flight_id}/samples", status_code=202)
def post_samples(flight_id: uuid.UUID, request: Request, body: dict = Body(...),
                 p: Principal = Depends(require(Perm.REQUEST_OWN))) -> JSONResponse:
    """Một lô mẫu (≤1 giây dữ liệu). Mẫu sai lược đồ bị loại và liệt kê trong `rejected`, mẫu đúng vẫn được nhận."""
    return _ingest(request, flight_id, body, p, backfill=False)


@router.post("/flights/{flight_id}/backfill", status_code=202)
def post_backfill(flight_id: uuid.UUID, request: Request, body: dict = Body(...),
                  p: Principal = Depends(require(Perm.REQUEST_OWN))) -> JSONResponse:
    """Gửi bù sau mất kết nối, giữ nhãn thời gian gốc (CT-8). Mẫu đã có (cùng chuyến, UAS ID, timeSource) bị bỏ qua."""
    return _ingest(request, flight_id, body, p, backfill=True)


@router.get("/flights/{flight_id}/rejected")
def rejected(flight_id: uuid.UUID, request: Request, p: Principal = Depends(require(Perm.REQUEST_OWN, Perm.FLIGHT_READ_ALL))) -> dict:
    """Mẫu bị loại và lý do, để khách sửa bộ phát."""
    _own_or_staff(request, p, flight_id)
    return {"items": [{"t_receive": iso_ms(r.t_receive), "channel": r.channel, "reason": r.reason}
                      for r in svc(request).rejected(flight_id)][-500:]}


class BindingIn(BaseModel):
    sysid: int = Field(ge=1, le=255, description="MAVLink system id của phương tiện")
    signing_key_hex: str | None = Field(default=None, pattern=r"^[0-9a-fA-F]{64}$",
                                        description="Khoá ký MAVLink v2 (32 byte). Có khoá thì B1 bỏ bản tin không ký hoặc sai chữ ký")


@router.post("/flights/{flight_id}/mavlink", status_code=201)
def bind_mavlink(flight_id: uuid.UUID, body: BindingIn, request: Request, p: Principal = Depends(require(Perm.REQUEST_OWN)),
                 s: Session = Depends(get_session)) -> dict:
    """Gán sysid MAVLink vào chuyến để cổng UDP biết bản tin thuộc chuyến nào."""
    t = svc(request)
    target = t.target(flight_id)
    try:
        t.check_target(target, customer_of(request, p), backfill=False)
    except IngestError as e:
        raise _problem(e)
    for old in s.scalars(select(MavlinkBinding).where(MavlinkBinding.sysid == body.sysid, MavlinkBinding.active.is_(True))):
        old.active = False
    b = MavlinkBinding(sysid=body.sysid, flight_id=flight_id, uas_id=target.uas_id,
                       signing_key_hex=body.signing_key_hex and body.signing_key_hex.lower(), created_by=p.user_id)
    s.add(b)
    s.commit()
    request.app.state.audit.record(p.actor(), "telemetry.mavlink.bind", target_type="flight", target_id=flight_id,
                                   after={"sysid": body.sysid, "signed": bool(body.signing_key_hex)})
    return {"id": str(b.id), "sysid": b.sysid, "flight_id": str(flight_id), "uas_id": b.uas_id, "signed": bool(b.signing_key_hex)}


# ---------- đọc cho vận hành ----------

def _own_or_staff(request: Request, p: Principal, flight_id: uuid.UUID) -> None:
    if has(p, Perm.FLIGHT_READ_ALL):
        return
    t = svc(request).target(flight_id)
    if t is None or t.customer_id != customer_of(request, p):
        raise HTTPException(404, "không có chuyến bay")


def _sample_out(x) -> dict:
    return {"t_source": iso_ms(x.t_source), "t_receive": iso_ms(x.t_receive), "t_ingest": iso_ms(x.t_ingest),
            "channel": x.channel, "backfill": x.backfill, "lat": x.lat, "lon": x.lon,
            "altitude": {"value": x.alt_value, "reference": x.alt_ref}, "alt_agl": x.alt_agl, "alt_amsl": x.alt_amsl,
            "ground_speed": x.ground_speed, "vertical_speed": x.vertical_speed, "track": x.track, "fix_type": x.fix_type,
            "satellites": x.satellites, "hdop": x.hdop, "battery_pct": x.battery_pct, "voltage": x.voltage, "mode": x.mode,
            "armed": x.armed, "safety_flags": x.safety_flags, "c2_state": x.c2_state}


@ops_router.get("/{flight_id}/telemetry")
def get_telemetry(flight_id: uuid.UUID, request: Request, start: dt.datetime | None = None, end: dt.datetime | None = None,
                  limit: int = Query(20_000, le=200_000),
                  p: Principal = Depends(require(Perm.FLIGHT_READ_ALL, Perm.REQUEST_OWN))) -> dict:
    """Mẫu đã lưu, kèm ba nhãn thời gian: tại nguồn, B1 nhận, ghi vào CSDL."""
    _own_or_staff(request, p, flight_id)
    rows = svc(request).samples(flight_id, start, end, limit)
    return {"flight_id": str(flight_id), "count": len(rows), "items": [_sample_out(x) for x in rows]}


@ops_router.get("/{flight_id}/timeline")
def get_timeline(flight_id: uuid.UUID, request: Request, start: dt.datetime | None = None, end: dt.datetime | None = None,
                 hz: float = Query(10.0, ge=1, le=20), p: Principal = Depends(require(Perm.FLIGHT_READ_ALL, Perm.REQUEST_OWN))) -> dict:
    """Trục thời gian chung (mặc định 10 Hz) với nhãn `measured` / `interpolated` / `gap` cho từng mốc."""
    _own_or_staff(request, p, flight_id)
    rows = [{"t_source": x.t_source, "lat": x.lat, "lon": x.lon, "alt_agl": x.alt_agl, "ground_speed": x.ground_speed}
            for x in svc(request).samples(flight_id, start, end)]
    pts = timeline.resample(rows, hz, request.app.state.settings.timeline_max_interp_gap_s, start, end)
    counts = {k: sum(1 for x in pts if x["kind"] == k) for k in ("measured", "interpolated", "gap")}
    return {"flight_id": str(flight_id), "hz": hz, "counts": counts,
            "items": [{**x, "t": iso_ms(x["t"]), "sample_t_source": x["sample_t_source"] and iso_ms(x["sample_t_source"])}
                      for x in pts]}

