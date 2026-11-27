from __future__ import annotations

import datetime as dt
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from b1.auth import Principal, has, require
from b1.connectors.i02 import NotAvailable
from b1.db import get_session
from b1.identity.roles import Perm
from b1.registry.models import CT_TESTS, DOC_KINDS, Aircraft, ConformanceResult, Customer, Document, Pilot

router = APIRouter(prefix="/v1/registry", tags=["Đăng ký và hồ sơ"])
OWN_OR_ALL = (Perm.REQUEST_OWN, Perm.FLIGHT_READ_ALL)


def dump(o) -> dict:
    return {c.name: getattr(o, c.name) for c in o.__table__.columns}


def _audit(request: Request, p: Principal, action: str, obj, before=None, after=None, outcome="ok", detail=None):
    request.app.state.audit.record(p.actor(), action, outcome, target_type=obj.__tablename__, target_id=obj.id,
                                   before=before, after=after, detail=detail)


def my_customer(request: Request, p: Principal) -> Customer | None:
    return request.app.state.registry.customer_of_org(p.org_id)


def check_own(request: Request, p: Principal, customer_id: uuid.UUID, write: bool = False) -> None:
    """Khách hàng chỉ thấy, sửa hồ sơ của mình; cán bộ có quyền đọc toàn bộ chỉ đọc."""
    if not write and has(p, Perm.FLIGHT_READ_ALL):
        return
    c = my_customer(request, p) if has(p, Perm.REQUEST_OWN) else None
    if c is None or c.id != customer_id:
        request.app.state.audit.record(p.actor(), "registry.access", "denied", target_type="customer", target_id=customer_id,
                                       detail={"reason": "không phải hồ sơ của mình", "path": request.url.path})
        raise HTTPException(403, "không phải hồ sơ của khách hàng này")


def _get(s: Session, model, id_):
    o = s.get(model, id_)
    if o is None:
        raise HTTPException(404, "không có")
    return o


# --- Khách hàng -------------------------------------------------------------------------------------------------------

class CustomerIn(BaseModel):
    org_id: uuid.UUID
    name: str = Field(min_length=1, max_length=200)
    tier: Literal["A", "B", "C"] = "C"
    kind: Literal["operator", "training"] = "operator"


class CustomerPatch(BaseModel):
    tier: Literal["A", "B", "C"] | None = None
    status: Literal["active", "suspended"] | None = None


@router.post("/customers", status_code=201)
def create_customer(body: CustomerIn, request: Request, p: Principal = Depends(require(Perm.CUSTOMER_MANAGE)),
                    s: Session = Depends(get_session)) -> dict:
    if s.scalar(select(Customer).where(Customer.org_id == body.org_id)):
        raise HTTPException(409, "tổ chức này đã có hồ sơ khách hàng")
    c = Customer(**body.model_dump())
    s.add(c)
    s.commit()
    _audit(request, p, "registry.customer.create", c, after=dump(c))
    return dump(c)


@router.patch("/customers/{customer_id}")
def patch_customer(customer_id: uuid.UUID, body: CustomerPatch, request: Request,
                   p: Principal = Depends(require(Perm.CUSTOMER_MANAGE)), s: Session = Depends(get_session)) -> dict:
    c = _get(s, Customer, customer_id)
    before = dump(c)
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(c, k, v)
    s.commit()
    _audit(request, p, "registry.customer.update", c, before, dump(c))
    return dump(c)


@router.get("/customers/me")
def get_my_customer(request: Request, p: Principal = Depends(require(Perm.REQUEST_OWN))) -> dict:
    c = my_customer(request, p)
    if c is None:
        raise HTTPException(404, "tài khoản chưa gắn với khách hàng")
    return dump(c)


# --- Người điều khiển, phương tiện ------------------------------------------------------------------------------------

class PilotIn(BaseModel):
    full_name: str = Field(min_length=1, max_length=200)
    unified_id: str | None = Field(default=None, max_length=64)


class AircraftIn(BaseModel):
    uas_id: str = Field(min_length=4, max_length=40, description="Số sê-ri ANSI/CTA-2063-A")
    vn_registration_no: str | None = Field(default=None, max_length=40)
    model: str | None = None
    mtow_kg: float = Field(gt=0)
    max_speed_ms: float = Field(gt=0)
    in_testing: bool = True
    flight_mgmt_device_id: str | None = None
    config_ref: str = Field(default="", max_length=100, description="Phiên bản phần mềm bay tự động và cấu hình vô tuyến")
    weather_limits: dict | None = None


class AircraftPatch(BaseModel):
    vn_registration_no: str | None = None
    in_testing: bool | None = None
    flight_mgmt_device_id: str | None = None
    config_ref: str | None = None
    weather_limits: dict | None = None


def _require_my_customer(request: Request, p: Principal) -> Customer:
    c = my_customer(request, p)
    if c is None:
        raise HTTPException(403, "tài khoản chưa gắn với khách hàng")
    if c.status != "active":
        raise HTTPException(403, "khách hàng đang bị tạm ngừng")
    return c


@router.post("/pilots", status_code=201)
def create_pilot(body: PilotIn, request: Request, p: Principal = Depends(require(Perm.REQUEST_OWN)),
                 s: Session = Depends(get_session)) -> dict:
    c = _require_my_customer(request, p)
    o = Pilot(customer_id=c.id, **body.model_dump())
    s.add(o)
    s.commit()
    _audit(request, p, "registry.pilot.create", o, after=dump(o))
    return dump(o)


@router.get("/pilots")
def list_pilots(request: Request, customer_id: uuid.UUID | None = None, p: Principal = Depends(require(*OWN_OR_ALL)),
                s: Session = Depends(get_session)) -> dict:
    if not has(p, Perm.FLIGHT_READ_ALL):
        customer_id = _require_my_customer(request, p).id
    q = select(Pilot) if customer_id is None else select(Pilot).where(Pilot.customer_id == customer_id)
    return {"items": [dump(o) for o in s.scalars(q).all()]}


@router.post("/aircraft", status_code=201)
def create_aircraft(body: AircraftIn, request: Request, p: Principal = Depends(require(Perm.REQUEST_OWN)),
                    s: Session = Depends(get_session)) -> dict:
    c = _require_my_customer(request, p)
    if s.scalar(select(Aircraft).where(Aircraft.uas_id == body.uas_id)):
        raise HTTPException(409, "UAS ID đã được đăng ký")
    o = Aircraft(customer_id=c.id, **body.model_dump())
    s.add(o)
    s.commit()
    _audit(request, p, "registry.aircraft.create", o, after=dump(o))
    return dump(o)


@router.get("/aircraft")
def list_aircraft(request: Request, customer_id: uuid.UUID | None = None, p: Principal = Depends(require(*OWN_OR_ALL)),
                  s: Session = Depends(get_session)) -> dict:
    if not has(p, Perm.FLIGHT_READ_ALL):
        customer_id = _require_my_customer(request, p).id
    q = select(Aircraft) if customer_id is None else select(Aircraft).where(Aircraft.customer_id == customer_id)
    return {"items": [dump(o) for o in s.scalars(q).all()]}


@router.get("/aircraft/{aircraft_id}")
def get_aircraft(aircraft_id: uuid.UUID, request: Request, p: Principal = Depends(require(*OWN_OR_ALL)),
                 s: Session = Depends(get_session)) -> dict:
    o = _get(s, Aircraft, aircraft_id)
    check_own(request, p, o.customer_id)
    return dump(o)


@router.patch("/aircraft/{aircraft_id}")
def patch_aircraft(aircraft_id: uuid.UUID, body: AircraftPatch, request: Request,
                   p: Principal = Depends(require(Perm.REQUEST_OWN)), s: Session = Depends(get_session)) -> dict:
    o = _get(s, Aircraft, aircraft_id)
    check_own(request, p, o.customer_id, write=True)
    before = dump(o)
    changes = body.model_dump(exclude_unset=True)
    for k, v in changes.items():
        setattr(o, k, v)
    if "flight_mgmt_device_id" in changes and changes["flight_mgmt_device_id"] != before["flight_mgmt_device_id"]:
        o.flight_mgmt_device_verified = False  # đổi thiết bị: phải xác nhận lại
    s.commit()
    _audit(request, p, "registry.aircraft.update", o, before, dump(o))
    return dump(o)


# --- Giấy tờ ----------------------------------------------------------------------------------------------------------

class DocumentIn(BaseModel):
    owner_type: Literal["aircraft", "pilot", "customer"]
    owner_id: uuid.UUID
    kind: Literal[DOC_KINDS]  # type: ignore[valid-type]
    number: str = Field(min_length=1, max_length=80)
    valid_from: dt.datetime | None = None
    valid_to: dt.datetime | None = None
    file_ref: str | None = None


class VerifyIn(BaseModel):
    decision: Literal["verified", "rejected"]
    note: str | None = Field(default=None, max_length=500)


def _owner_customer(s: Session, owner_type: str, owner_id: uuid.UUID) -> uuid.UUID:
    model = {"aircraft": Aircraft, "pilot": Pilot, "customer": Customer}[owner_type]
    o = _get(s, model, owner_id)
    return o.id if owner_type == "customer" else o.customer_id


@router.post("/documents", status_code=201)
def submit_document(body: DocumentIn, request: Request, p: Principal = Depends(require(Perm.REQUEST_OWN)),
                    s: Session = Depends(get_session)) -> dict:
    cid = _owner_customer(s, body.owner_type, body.owner_id)
    check_own(request, p, cid, write=True)
    d = Document(customer_id=cid, status="submitted", **body.model_dump())
    s.add(d)
    s.commit()
    _audit(request, p, "registry.document.submit", d, after=dump(d))
    return dump(d)


@router.get("/documents")
def list_documents(request: Request, customer_id: uuid.UUID | None = None, status: str | None = None,
                   p: Principal = Depends(require(*OWN_OR_ALL)), s: Session = Depends(get_session)) -> dict:
    if not has(p, Perm.FLIGHT_READ_ALL):
        customer_id = _require_my_customer(request, p).id
    q = select(Document)
    if customer_id:
        q = q.where(Document.customer_id == customer_id)
    if status:
        q = q.where(Document.status == status)
    return {"items": [dump(o) for o in s.scalars(q.order_by(Document.created_at)).all()]}


@router.post("/documents/{document_id}/verify")
def verify_document(document_id: uuid.UUID, body: VerifyIn, request: Request,
                    p: Principal = Depends(require(Perm.REGISTRY_VERIFY)), s: Session = Depends(get_session)) -> dict:
    d = _get(s, Document, document_id)
    before = dump(d)
    d.status, d.note = body.decision, body.note
    d.verified_by, d.verified_via, d.verified_at = p.user_id, "manual", dt.datetime.now(dt.timezone.utc)
    s.commit()
    _audit(request, p, "registry.document.verify", d, before, dump(d))
    return dump(d)


@router.post("/aircraft/{aircraft_id}/verify-device")
def verify_device(aircraft_id: uuid.UUID, request: Request, p: Principal = Depends(require(Perm.REGISTRY_VERIFY)),
                  s: Session = Depends(get_session)) -> dict:
    o = _get(s, Aircraft, aircraft_id)
    if not o.flight_mgmt_device_id:
        raise HTTPException(409, "phương tiện chưa khai thiết bị quản lý hoạt động bay")
    before = dump(o)
    o.flight_mgmt_device_verified = True
    s.commit()
    _audit(request, p, "registry.aircraft.verify_device", o, before, dump(o), detail={"via": "manual"})
    return dump(o)


@router.post("/aircraft/{aircraft_id}/verify-registration")
def verify_registration(aircraft_id: uuid.UUID, request: Request,
                        p: Principal = Depends(require(Perm.REQUEST_OWN, Perm.REGISTRY_VERIFY)),
                        s: Session = Depends(get_session)) -> dict:
    """Tra số đăng ký Việt Nam qua mô-đun I-02. Khớp sê-ri và còn hiệu lực thì tạo giấy chứng nhận đăng ký đã xác nhận."""
    o = _get(s, Aircraft, aircraft_id)
    if not has(p, Perm.REGISTRY_VERIFY):
        check_own(request, p, o.customer_id, write=True)
    if not o.vn_registration_no:
        raise HTTPException(409, "phương tiện chưa khai số đăng ký Việt Nam")
    conn = request.app.state.i02
    try:
        rec = conn.lookup(o.vn_registration_no)
    except NotAvailable as e:
        raise HTTPException(503, str(e))
    problems = []
    if rec is None:
        problems.append("không có trong hệ thống đăng ký")
    else:
        if rec.uas_serial != o.uas_id:
            problems.append("số sê-ri không khớp UAS ID")
        if rec.status != "valid":
            problems.append(f"trạng thái đăng ký: {rec.status}")
    detail = {"connector": conn.name, "registration_no": o.vn_registration_no, "record": rec and rec.model_dump(mode="json")}
    if problems:
        _audit(request, p, "registry.aircraft.verify_registration", o, outcome="rejected", detail={**detail, "problems": problems})
        return {"verified": False, "problems": problems}
    d = Document(customer_id=o.customer_id, owner_type="aircraft", owner_id=o.id, kind="registration_certificate",
                 number=rec.registration_no, valid_from=rec.valid_from, valid_to=rec.valid_to, status="verified",
                 verified_via="I-02", verified_at=dt.datetime.now(dt.timezone.utc), note=conn.name)
    s.add(d)
    before = dump(o)
    if rec.flight_mgmt_device_id and rec.flight_mgmt_device_id == o.flight_mgmt_device_id:
        o.flight_mgmt_device_verified = True
    s.commit()
    _audit(request, p, "registry.aircraft.verify_registration", o, before, dump(o), detail={**detail, "document_id": str(d.id)})
    return {"verified": True, "document": dump(d), "flight_mgmt_device_verified": o.flight_mgmt_device_verified}


# --- CT-1…8 -----------------------------------------------------------------------------------------------------------

class ConformanceIn(BaseModel):
    test: Literal[CT_TESTS]  # type: ignore[valid-type]
    passed: bool
    tested_at: dt.datetime
    measured: dict | None = None
    report_ref: str | None = None


@router.post("/aircraft/{aircraft_id}/conformance", status_code=201)
def record_conformance(aircraft_id: uuid.UUID, body: ConformanceIn, request: Request,
                       p: Principal = Depends(require(Perm.CONFORMANCE_RECORD)), s: Session = Depends(get_session)) -> dict:
    """Biên bản có hiệu lực 12 tháng hoặc đến khi đổi phương tiện, phần mềm bay tự động hay cấu hình vô tuyến."""
    o = _get(s, Aircraft, aircraft_id)
    r = ConformanceResult(aircraft_id=o.id, test=body.test, passed=body.passed, tested_at=body.tested_at,
                          valid_until=body.tested_at + dt.timedelta(days=365), measured=body.measured,
                          report_ref=body.report_ref, config_ref=o.config_ref, recorded_by=p.user_id)
    s.add(r)
    s.commit()
    _audit(request, p, "registry.conformance.record", r, after=dump(r))
    return dump(r)


@router.get("/eligibility")
def eligibility(aircraft_id: uuid.UUID, pilot_id: uuid.UUID, start: dt.datetime, end: dt.datetime, request: Request,
                bvlos: bool = True, stage: Literal["request", "final"] = "request",
                p: Principal = Depends(require(*OWN_OR_ALL)), s: Session = Depends(get_session)) -> dict:
    """Xem trước điều kiện hồ sơ cho một khung bay. Việc chặn và ghi lý do do dịch vụ xếp lịch làm."""
    o = _get(s, Aircraft, aircraft_id)
    check_own(request, p, o.customer_id)
    return request.app.state.registry.eligibility(aircraft_id, pilot_id, start, end, bvlos, stage).as_dict()
