"""Cảnh báo (F-06) và bản đồ trực tiếp (F-05): danh sách, xác nhận, luồng SSE cảnh báo và trạng thái trực tiếp."""
from __future__ import annotations

import asyncio
import json
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from b1.auth import Principal, require
from b1.identity.roles import Perm
from b1.monitoring.service import MonitorService

router = APIRouter(prefix="/v1", tags=["Giám sát"])


def svc(request: Request) -> MonitorService:
    return request.app.state.monitor


@router.get("/alerts")
def list_alerts(request: Request, open_only: bool = Query(False, alias="open"), flight_id: uuid.UUID | None = None,
                kind: str | None = None, limit: int = Query(200, le=2000),
                p: Principal = Depends(require(Perm.FLIGHT_READ_ALL))) -> dict:
    return {"items": svc(request).alerts(open_only=open_only, flight_id=flight_id, kind=kind, limit=limit)}


class AckIn(BaseModel):
    resolve: bool = False
    note: str | None = Field(default=None, max_length=1000)


@router.post("/alerts/{alert_id}/acknowledge")
def acknowledge(alert_id: uuid.UUID, body: AckIn, request: Request, p: Principal = Depends(require(Perm.ALERT_ACK))) -> dict:
    """Người vận hành xác nhận đã thấy (và có thể đóng) cảnh báo; ghi nhật ký."""
    a = svc(request).acknowledge(p.actor(), alert_id, body.resolve, body.note)
    if a is None:
        raise HTTPException(404, "không có cảnh báo")
    return a


@router.get("/alerts/events")
def alert_events(request: Request, after: int = 0, limit: int = Query(500, le=2000),
                 p: Principal = Depends(require(Perm.FLIGHT_READ_ALL))) -> dict:
    m = svc(request)
    return {"items": m.events_after(after, limit), "last_seq": m.last_seq()}


@router.get("/live")
def live(request: Request, p: Principal = Depends(require(Perm.FLIGHT_READ_ALL))) -> dict:
    return snapshot(request)


def snapshot(request: Request) -> dict:
    m = svc(request)
    sv = request.app.state.surveillance
    return {"t": time.time(), "flights": m.live_flights(), **sv.tracks(), "stations": sv.stations()}


@router.get("/live/stream")
async def live_stream(request: Request, after: int | None = None, hz: float = Query(5.0, ge=0.5, le=10),
                      p: Principal = Depends(require(Perm.FLIGHT_READ_ALL))):
    """Luồng SSE cho bản đồ trực tiếp: sự kiện cảnh báo ngay khi có (`alert`), ảnh chụp vị trí `hz` lần mỗi giây (`live`)."""
    m = svc(request)
    poll = request.app.state.settings.alert_poll_s

    async def gen():
        seq = after if after is not None else await asyncio.to_thread(m.last_seq)
        yield f"event: hello\ndata: {json.dumps({'seq': seq})}\n\n"
        next_snap = 0.0
        while not await request.is_disconnected():
            for e in await asyncio.to_thread(m.events_after, seq):
                seq = e["seq"]
                yield f"id: {seq}\nevent: alert\ndata: {json.dumps(e, ensure_ascii=False, default=str)}\n\n"
            now = time.monotonic()
            if now >= next_snap:
                next_snap = now + 1.0 / hz
                snap = await asyncio.to_thread(snapshot, request)
                yield f"event: live\ndata: {json.dumps(snap, ensure_ascii=False, default=str)}\n\n"
            await asyncio.sleep(poll)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
