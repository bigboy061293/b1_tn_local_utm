"""I-06 Dữ liệu trạm RIS (hợp đồng api/i06-ris-station.openapi.yaml) và đọc vết, trạng thái trạm, đối chiếu Remote ID.

Trạm là tài khoản máy có vai trò `ris_station`; token mang claim `station_id` của đúng trạm (cấu hình ở bên cấp danh tính),
chứng chỉ thiết bị kiểm ở cổng vào (mTLS).
"""
from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from b1.auth import Principal, require
from b1.identity.roles import Perm
from b1.surveillance.service import STATIONS, SurveillanceService

router = APIRouter(prefix="/ris/v1", tags=["I-06 Trạm RIS"])
ops_router = APIRouter(prefix="/v1/surveillance", tags=["Giám sát"])


def svc(request: Request) -> SurveillanceService:
    return request.app.state.surveillance


@router.post("/stations/{station_id}/records", status_code=202)
def post_records(station_id: Literal[STATIONS], request: Request, body: dict = Body(...),  # type: ignore[valid-type]
                 p: Principal = Depends(require(Perm.STATION_SUBMIT))) -> JSONResponse:
    if p.claims.get("station_id") != station_id:
        request.app.state.audit.record(p.actor(), "ris.records.submit", "denied", target_type="station", target_id=station_id,
                                       detail={"reason": "token không thuộc trạm này"})
        raise HTTPException(403, "token không thuộc trạm này")
    recs = body.get("records") if isinstance(body, dict) else None
    if not isinstance(recs, list) or not 1 <= len(recs) <= 1000:
        raise HTTPException(400, "thân yêu cầu phải là {\"records\": [...]} với 1–1000 bản ghi")
    ack = svc(request).ingest(station_id, recs, backfill=bool(body.get("backfill")))
    ack.pop("alerts", None)
    return JSONResponse(ack, status_code=202)


@ops_router.get("/tracks")
def tracks(request: Request, p: Principal = Depends(require(Perm.FLIGHT_READ_ALL))) -> dict:
    """Vết ADS-B đã gộp và vết Broadcast Remote ID. `status: degraded` khi quá 15 giây, kèm vòng bất định."""
    return svc(request).tracks()


@ops_router.get("/stations")
def stations(request: Request, p: Principal = Depends(require(Perm.AIRSPACE_READ))) -> dict:
    """Trạng thái 5 trạm và mức suy giảm (L0, L1 4G/5G, L2 mất trạm, L4 bộ lưu điện), thời tiết đo gần nhất."""
    return {"items": svc(request).stations()}


@ops_router.get("/flights/{flight_id}/rid-reconciliation")
def reconciliation(flight_id: uuid.UUID, request: Request, p: Principal = Depends(require(Perm.FLIGHT_READ_ALL))) -> dict:
    """Tỷ lệ đối chiếu Broadcast Remote ID với telemetry của chuyến (AT-05)."""
    r = svc(request).reconciliation(flight_id)
    if r is None:
        raise HTTPException(404, "chưa có bản tin Remote ID nào của chuyến này")
    return r
