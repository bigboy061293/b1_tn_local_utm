"""Chuyến bay, cho phép cất cánh (F-04, AT-04) và lịch trực."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from b1.auth import Principal, require
from b1.db import get_session
from b1.identity.roles import Perm, Role
from b1.operations.models import Clearance, DutyShift, Flight

router = APIRouter(prefix="/v1/flights", tags=["Chuyến bay"])
duty_router = APIRouter(prefix="/v1/duty", tags=["Lịch trực"])

# F-04: danh mục kiểm tra trước khi cho phép cất cánh.
CHECKLIST = {
    "weather": "Thời tiết trong giới hạn của phương tiện trên đoạn tuyến và lớp độ cao",
    "ris_stations": "Trạm RIS trên tuyến hoạt động",
    "network": "Mạng, đường truyền dẫn và lớp lưu lượng an toàn sẵn sàng",
    "conformance_monitoring": "Giám sát tuân thủ của chuyến đang chạy",
    "emergency_plan": "Phương án khẩn nguy của chuyến đã sẵn sàng",
}


def _out(f: Flight) -> dict:
    return {"id": str(f.id), "request_ref": f.request_ref, "uas_id": f.uas_id, "state": f.state,
            "starts_at": f.starts_at, "ends_at": f.ends_at, "block_ids": f.block_ids, "bvlos": f.bvlos,
            "takeoff_cleared_by": f.takeoff_cleared_by and str(f.takeoff_cleared_by),
            "takeoff_cleared_at": f.takeoff_cleared_at, "version": f.version}


@router.get("/{flight_id}")
def get_flight(flight_id: uuid.UUID, p: Principal = Depends(require(Perm.FLIGHT_READ_ALL)), s: Session = Depends(get_session)) -> dict:
    f = s.get(Flight, flight_id)
    if not f:
        raise HTTPException(404, "không có chuyến bay")
    return _out(f)


class CheckItem(BaseModel):
    ok: bool
    note: str | None = Field(default=None, max_length=1000)


class ClearanceIn(BaseModel):
    weather: CheckItem
    ris_stations: CheckItem
    network: CheckItem
    conformance_monitoring: CheckItem
    emergency_plan: CheckItem
    confirm: Literal[True] = Field(description="Người cho phép xác nhận đã kiểm tra và ký")
    statement: str | None = Field(default=None, max_length=1000)


def _canon(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode()


def _iso(t: dt.datetime | None) -> str | None:
    return t.astimezone(dt.timezone.utc).isoformat(timespec="seconds") if t else None


def preconditions(request: Request, p: Principal, f: Flight, body: ClearanceIn, now: dt.datetime) -> list[dict]:
    """Điều kiện B1 tự kiểm trước khi cho ký. Trả từng điều kiện kèm kết quả để ghi vào bản ghi và nhật ký."""
    st = request.app.state
    lead = dt.timedelta(minutes=st.airspace.params.clearance_lead_min)
    out = []
    if not (f.starts_at and f.ends_at and f.block_ids):
        out.append({"code": "WINDOW", "ok": False, "detail": "chuyến chưa có khung giờ và khối đã xếp lịch"})
    else:
        ok = f.starts_at - lead <= now < f.ends_at
        out.append({"code": "WINDOW", "ok": ok, "detail": f"được cho phép từ {_iso(f.starts_at - lead)} đến trước {_iso(f.ends_at)}"})
        missing = st.airspace.uncovered_blocks(f.block_ids, f.starts_at, f.ends_at)
        out.append({"code": "BLOCKS_ACTIVE", "ok": not missing,
                    "detail": "mọi khối của chuyến đã kích hoạt phủ trọn khung giờ" if not missing
                    else f"khối chưa kích hoạt phủ khung giờ: {', '.join(missing)}"})
    holders = [r for r in (Role.DUTY_SAFETY_OFFICER, Role.CORRIDOR_HEAD) if r in p.roles]
    need = st.settings.duty_required_roles
    duty_ok = any(r.value not in need or st.operations.on_duty(p.user_id, r.value, now) for r in holders)
    out.append({"code": "ON_DUTY", "ok": duty_ok, "detail": "đang trong ca trực" if duty_ok else
                f"vai trò {', '.join(r.value for r in holders)} phải có ca trực đang hiệu lực"})
    bad = [k for k in CHECKLIST if not getattr(body, k).ok]
    out.append({"code": "CHECKLIST", "ok": not bad, "detail": "đủ 5 mục" if not bad else f"mục chưa đạt: {', '.join(bad)}"})
    auth_time = p.claims.get("auth_time")
    max_age = st.settings.signature_max_auth_age_s
    fresh = isinstance(auth_time, (int, float)) and now.timestamp() - auth_time <= max_age
    out.append({"code": "SIGNER_REAUTH", "ok": fresh, "detail": f"đăng nhập lại trong {max_age // 60} phút trước khi ký" if fresh
                else f"phải đăng nhập lại (claim auth_time) trong {max_age // 60} phút trước khi ký"})
    return out


@router.post("/{flight_id}/takeoff-clearance")
def clear_takeoff(flight_id: uuid.UUID, body: ClearanceIn, request: Request,
                  p: Principal = Depends(require(Perm.FLIGHT_CLEAR_TAKEOFF, target_type="flight", target_param="flight_id")),
                  s: Session = Depends(get_session)) -> dict:
    """F-04: chỉ Cán bộ an toàn bay trực ban và Trưởng bộ phận (khi trực), có MFA; danh mục kiểm tra; ký điện tử.

    Không phải phép bay (Đặc tả Mục 1). Bản ghi đã ký đọc lại ở GET /v1/flights/{id}/clearance.
    """
    audit = request.app.state.audit
    now = dt.datetime.now(dt.timezone.utc)
    f = s.get(Flight, flight_id, with_for_update=True)
    if not f:
        raise HTTPException(404, "không có chuyến bay")
    before = _out(f)
    if f.state != "scheduled":
        audit.record(p.actor(), Perm.FLIGHT_CLEAR_TAKEOFF.value, "rejected", target_type="flight", target_id=f.id, before=before,
                     detail={"reason": f"trạng thái {f.state}"})
        raise HTTPException(409, f"chuyến bay đang ở trạng thái {f.state}")
    pre = preconditions(request, p, f, body, now)
    failed = [x for x in pre if not x["ok"]]
    if failed:
        audit.record(p.actor(), Perm.FLIGHT_CLEAR_TAKEOFF.value, "rejected", target_type="flight", target_id=f.id, before=before,
                     detail={"reason": "chưa đủ điều kiện", "preconditions": pre})
        raise HTTPException(409, {"message": "chưa đủ điều kiện cho phép cất cánh", "failed": failed, "preconditions": pre})
    record = {
        "type": "b1.takeoff_clearance", "flight_id": str(f.id), "flight_version": f.version, "queue_no": f.request_ref,
        "uas_id": f.uas_id, "window": {"start": _iso(f.starts_at), "end": _iso(f.ends_at)}, "block_ids": f.block_ids,
        "checklist": {k: {"label": v, **getattr(body, k).model_dump()} for k, v in CHECKLIST.items()},
        "preconditions": pre, "statement": body.statement,
        "signer": {"user_id": str(p.user_id), "subject": p.subject, "display_name": p.display_name,
                   "roles": sorted(r.value for r in p.roles), "amr": p.claims.get("amr"), "auth_time": p.claims.get("auth_time"),
                   "issuer": p.claims.get("iss")},
        "signed_at": _iso(now),
        "disclaimer": "\"Phê duyệt\" trên B1 là phê duyệt lịch bay nội bộ, không phải phép bay.",
    }
    digest = hashlib.sha256(_canon(record)).hexdigest()
    jws = request.app.state.signer.sign({"iss": "b1", "iat": int(now.timestamp()), "sha256": digest, "record": record})
    f.state, f.takeoff_cleared_by, f.takeoff_cleared_at = "cleared", p.user_id, now
    f.version += 1
    c = Clearance(flight_id=f.id, record=record, record_sha256=digest, signature=jws, signed_by=p.user_id, signed_at=now)
    s.add(c)
    s.commit()
    audit.record(p.actor(), Perm.FLIGHT_CLEAR_TAKEOFF.value, target_type="flight", target_id=f.id, before=before, after=_out(f),
                 detail={"clearance_id": str(c.id), "sha256": digest})
    return {**_out(f), "clearance": {"id": str(c.id), "sha256": digest, "signature": jws}}


@router.get("/{flight_id}/clearance")
def get_clearance(flight_id: uuid.UUID, request: Request, p: Principal = Depends(require(Perm.FLIGHT_READ_ALL)),
                  s: Session = Depends(get_session)) -> dict:
    c = s.scalar(select(Clearance).where(Clearance.flight_id == flight_id))
    if not c:
        raise HTTPException(404, "chuyến chưa được cho phép cất cánh")
    try:
        claims = request.app.state.signer.verify(c.signature)
        valid = claims["sha256"] == c.record_sha256 == hashlib.sha256(_canon(claims["record"])).hexdigest() \
            and claims["record"] == c.record
    except Exception:  # noqa: BLE001  chữ ký hỏng thì báo không hợp lệ, không ném lỗi
        valid = False
    return {"id": str(c.id), "flight_id": str(flight_id), "record": c.record, "sha256": c.record_sha256, "signature": c.signature,
            "signature_valid": valid, "signed_at": c.signed_at}


# ---------- lịch trực ----------

class ShiftIn(BaseModel):
    user_id: uuid.UUID
    role: Literal["corridor_head", "duty_safety_officer"]
    starts_at: dt.datetime
    ends_at: dt.datetime

    @model_validator(mode="after")
    def _check(self):
        if self.starts_at.tzinfo is None or self.ends_at.tzinfo is None:
            raise ValueError("giờ phải có múi giờ (RFC 3339)")
        if self.ends_at <= self.starts_at or self.ends_at - self.starts_at > dt.timedelta(hours=24):
            raise ValueError("ca trực phải dài hơn 0 và không quá 24 giờ")
        return self


def _shift(x: DutyShift) -> dict:
    return {"id": str(x.id), "user_id": str(x.user_id), "role": x.role, "starts_at": x.starts_at, "ends_at": x.ends_at,
            "cancelled": x.cancelled}


@duty_router.post("/shifts", status_code=201)
def create_shift(body: ShiftIn, request: Request, p: Principal = Depends(require(Perm.DUTY_MANAGE)),
                 s: Session = Depends(get_session)) -> dict:
    x = DutyShift(user_id=body.user_id, role=body.role, starts_at=body.starts_at, ends_at=body.ends_at, created_by=p.user_id)
    s.add(x)
    s.commit()
    request.app.state.audit.record(p.actor(), "duty.shift.create", target_type="duty_shift", target_id=x.id, after=_shift(x))
    return _shift(x)


@duty_router.get("/shifts")
def list_shifts(at: dt.datetime | None = None, p: Principal = Depends(require(Perm.FLIGHT_READ_ALL)),
                s: Session = Depends(get_session)) -> dict:
    q = select(DutyShift).where(DutyShift.cancelled.is_(False)).order_by(DutyShift.starts_at)
    if at:
        q = q.where(DutyShift.starts_at <= at, DutyShift.ends_at > at)
    return {"items": [_shift(x) for x in s.scalars(q)]}


@duty_router.post("/shifts/{shift_id}/cancel")
def cancel_shift(shift_id: uuid.UUID, request: Request, p: Principal = Depends(require(Perm.DUTY_MANAGE)),
                 s: Session = Depends(get_session)) -> dict:
    x = s.get(DutyShift, shift_id, with_for_update=True)
    if not x:
        raise HTTPException(404, "không có ca trực")
    x.cancelled = True
    s.commit()
    request.app.state.audit.record(p.actor(), "duty.shift.cancel", target_type="duty_shift", target_id=x.id, after=_shift(x))
    return _shift(x)
