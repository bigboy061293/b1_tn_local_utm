"""I-01: cổng đăng ký bay và dữ liệu của tỉnh gửi hồ sơ vào B1 (hợp đồng `api/i01-i09-province-training.openapi.yaml`).

Cổng tỉnh là tài khoản máy (OAuth 2.0 client credentials) có vai trò `province_portal` trong B1 và phạm vi quyền
`province:applications.write` / `.read` trong token. Hồ sơ đi qua đúng luồng như khách nộp trên cổng khách hàng:
giấy tờ vào trạng thái "đã nộp" chờ cán bộ xác nhận, yêu cầu bay qua kiểm hồ sơ và bị chặn nếu thiếu (AT-01).
"""
from __future__ import annotations

import datetime as dt
import uuid
from collections import defaultdict
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from b1.auth import Principal, require
from b1.db import get_session
from b1.gateway.models import Application
from b1.identity.roles import Perm
from b1.registry.models import Aircraft, Customer, Document, Pilot
from b1.scheduling.calendar import TZ
from b1.scheduling.models import BlockReason, FlightRequest, RequestItem
from b1.scheduling.service import ItemIn, RequestError

router = APIRouter(prefix="/gateway/v1", tags=["I-01 Cổng tỉnh"])

DOC_KIND = {"registration": "registration_certificate", "temporary_registration": "temporary_registration",
            "technical_status": "technical_status_certificate", "pilot_licence": "pilot_licence", "test_permit": "test_permit",
            "flight_permit": "flight_permit", "insurance": "insurance"}
DOC_OWNER = {"registration_certificate": "aircraft", "temporary_registration": "aircraft", "technical_status_certificate": "aircraft",
             "flight_permit": "aircraft", "pilot_licence": "pilot", "test_permit": "customer", "insurance": "customer"}


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CustomerRef(_M):
    unifiedId: str
    name: str


class PilotRef(_M):
    unifiedId: str
    licenceNo: str | None = None


class AircraftRef(_M):
    uasId: str = Field(min_length=4, max_length=40)
    vnRegistrationNo: str
    mtowKg: float = Field(gt=0)
    maxSpeedMs: float | None = Field(default=None, gt=0)


class SlotRef(_M):
    date: dt.date
    slotIndex: int = Field(ge=1, le=4)
    blockIds: list[Literal["block-1", "block-2", "block-3", "block-4"]] = Field(min_length=1)


class DocRef(_M):
    kind: Literal[tuple(DOC_KIND)]  # type: ignore[valid-type]
    number: str
    validTo: dt.date
    fileUrl: str | None = None


class ApplicationIn(_M):
    externalRef: str = Field(min_length=1, max_length=100)
    customer: CustomerRef
    pilot: PilotRef
    aircraft: AircraftRef
    priorityGroup: int = Field(ge=1, le=4)
    bvlos: bool = True
    training: bool = False
    requestedSlots: list[SlotRef] = Field(min_length=1)
    documents: list[DocRef] = []


def _scope(p: Principal, scope: str) -> None:
    if scope not in str(p.claims.get("scope", "")).split():
        raise HTTPException(403, f"token thiếu phạm vi quyền {scope}")


def status_of(request: Request, s: Session, a: Application) -> dict:
    """ApplicationStatus theo hợp đồng I-01."""
    out = {"applicationId": str(a.id), "externalRef": a.external_ref}
    if a.state == "rejected":
        return {**out, "state": "rejected", "blockReasons": a.reject_reasons}
    reqs = [s.get(FlightRequest, uuid.UUID(r)) for r in a.request_ids]
    items = s.scalars(select(RequestItem).where(RequestItem.request_id.in_([r.id for r in reqs]))).all()
    st, ist = {r.status for r in reqs}, {i.status for i in items}
    if "provisional" in st:
        state = "incomplete"
    elif "scheduled" in ist:
        state = "scheduled"
    elif st & {"queued", "partial"}:
        state = "queued"
    elif st == {"cancelled"}:
        state = "closed"
    else:
        state = "blocked"
    latest: dict[str, BlockReason] = {}
    for b in s.scalars(select(BlockReason).where(BlockReason.request_id.in_([r.id for r in reqs])).order_by(BlockReason.at)).all():
        latest[b.rule] = b
    official = [r for r in reqs if not r.provisional]
    return {**out, "state": state, "provisional": any(r.provisional for r in reqs),
            **({"queueNo": official[0].queue_no} if official else {}),
            "queueNos": [r.queue_no for r in reqs],
            "blockReasons": [{"rule": b.rule, "detail": b.detail} for b in latest.values()] if state == "incomplete" else [],
            "scheduledSlots": [{"startsAt": i.starts_at, "endsAt": i.ends_at,
                                "blockIds": next(r.block_ids for r in reqs if r.id == i.request_id)}
                               for i in items if i.status == "scheduled"],
            "reports": []}


@router.post("/applications", status_code=201)
def submit_application(body: ApplicationIn, request: Request, response: Response,
                       p: Principal = Depends(require(Perm.PROVINCE_SUBMIT)), s: Session = Depends(get_session)) -> dict:
    _scope(p, "province:applications.write")
    audit = request.app.state.audit
    old = s.scalar(select(Application).where(Application.external_ref == body.externalRef))
    if old:  # gửi lại cùng mã hồ sơ: trả kết quả cũ, không tạo trùng
        response.status_code = 200
        return status_of(request, s, old)

    app_row = Application(external_ref=body.externalRef, client_subject=p.subject, payload=body.model_dump(mode="json"),
                          state="received", request_ids=[], reject_reasons=[])
    cu = s.scalar(select(Customer).where(Customer.unified_id == body.customer.unifiedId))
    reasons = []
    if cu is None:
        reasons.append({"rule": "CUSTOMER_UNKNOWN", "detail": "khách hàng chưa được bộ phận quản lý hành lang lập hồ sơ"})
    elif cu.status != "active":
        reasons.append({"rule": "CUSTOMER_SUSPENDED", "detail": cu.name})
    ac = s.scalar(select(Aircraft).where(Aircraft.uas_id == body.aircraft.uasId))
    if cu and ac and ac.customer_id != cu.id:
        reasons.append({"rule": "AIRCRAFT_OTHER_CUSTOMER", "detail": body.aircraft.uasId})
    if ac is None and body.aircraft.maxSpeedMs is None:
        reasons.append({"rule": "AIRCRAFT_INCOMPLETE", "detail": "phương tiện mới phải có maxSpeedMs"})
    if reasons:
        app_row.state, app_row.reject_reasons = "rejected", reasons
        s.add(app_row)
        s.commit()
        audit.record(p.actor(), "i01.application.submit", "rejected", target_type="application", target_id=app_row.id,
                     detail={"externalRef": body.externalRef, "reasons": reasons})
        return status_of(request, s, app_row)

    created = []
    if ac is None:
        ac = Aircraft(customer_id=cu.id, uas_id=body.aircraft.uasId, vn_registration_no=body.aircraft.vnRegistrationNo,
                      mtow_kg=body.aircraft.mtowKg, max_speed_ms=body.aircraft.maxSpeedMs)
        s.add(ac)
        created.append("aircraft")
    pi = s.scalar(select(Pilot).where(Pilot.customer_id == cu.id, Pilot.unified_id == body.pilot.unifiedId))
    if pi is None:
        pi = Pilot(customer_id=cu.id, unified_id=body.pilot.unifiedId, full_name=f"(theo I-01) {body.pilot.unifiedId}")
        s.add(pi)
        created.append("pilot")
    s.flush()
    owners = {"aircraft": ac.id, "pilot": pi.id, "customer": cu.id}
    for d in body.documents:
        kind = DOC_KIND[d.kind]
        otype = DOC_OWNER[kind]
        if s.scalar(select(Document).where(Document.owner_id == owners[otype], Document.kind == kind, Document.number == d.number)):
            continue
        valid_to = dt.datetime(d.validTo.year, d.validTo.month, d.validTo.day, 23, 59, 59, tzinfo=TZ)
        s.add(Document(customer_id=cu.id, owner_type=otype, owner_id=owners[otype], kind=kind, number=d.number,
                       valid_to=valid_to, file_ref=d.fileUrl, status="submitted", note=f"I-01 {body.externalRef}"))
    s.commit()

    # Mỗi bộ khối khác nhau thành một yêu cầu bay riêng.
    groups: dict[tuple, list[ItemIn]] = defaultdict(list)
    for sl in body.requestedSlots:
        groups[tuple(sorted(sl.blockIds))].append(ItemIn(sl.date, sl.slotIndex - 1))
    svc = request.app.state.scheduling
    try:
        for blocks, items in groups.items():
            r, _ = svc.submit(p.actor(), cu.id, aircraft_id=ac.id, pilot_id=pi.id, priority_group=body.priorityGroup,
                              bvlos=body.bvlos, block_ids=list(blocks), items=items)
            app_row.request_ids = [*app_row.request_ids, str(r.id)]
    except RequestError as e:
        app_row.state = "rejected"
        app_row.reject_reasons = [{"rule": "INVALID_REQUEST", "detail": str(e)}]
        for rid in app_row.request_ids:  # không để lại yêu cầu dở dang
            svc.cancel(p.actor(), uuid.UUID(rid))
    app_row.customer_id = cu.id
    s.add(app_row)
    s.commit()
    audit.record(p.actor(), "i01.application.submit", "ok" if app_row.state != "rejected" else "rejected",
                 target_type="application", target_id=app_row.id,
                 detail={"externalRef": body.externalRef, "created": created, "requests": app_row.request_ids,
                         "reasons": app_row.reject_reasons})
    return status_of(request, s, app_row)


@router.get("/applications/{application_id}")
def get_application(application_id: uuid.UUID, request: Request, p: Principal = Depends(require(Perm.PROVINCE_SUBMIT)),
                    s: Session = Depends(get_session)) -> dict:
    _scope(p, "province:applications.read")
    a = s.get(Application, application_id)
    if a is None:
        raise HTTPException(404, "không có hồ sơ")
    return status_of(request, s, a)
