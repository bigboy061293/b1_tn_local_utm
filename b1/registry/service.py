"""Kiểm điều kiện hồ sơ trước khi xếp lịch (F-01; Đặc tả Mục 4.3 "Điều kiện chặn"). Giao diện cho dịch vụ xếp lịch.

Mỗi điều kiện thiếu trả về một mã quy tắc để xếp lịch ghi lý do chặn (AT-01). Chỉ giấy tờ đã được xác nhận
(thủ công hoặc qua I-02) mới được tính.
"""
from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass, field

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, sessionmaker

from b1.registry.models import CT_TESTS, Aircraft, ConformanceResult, Customer, Document, Pilot

MTOW_LICENCE_KG = 2.0  # người điều khiển cần giấy phép khi bay ngoài tầm nhìn hoặc phương tiện từ 2 kg

RULES = {
    "OWNERSHIP": "Phương tiện, người điều khiển phải thuộc khách hàng đang hoạt động",
    "REGISTRATION": "Thiếu Giấy chứng nhận đăng ký hoặc đăng ký tạm thời còn hiệu lực",
    "TECHNICAL_STATUS": "Phương tiện đang thử nghiệm thiếu Giấy xác nhận tình trạng kỹ thuật còn hiệu lực",
    "PILOT_LICENCE": "Người điều khiển thiếu giấy phép (bay ngoài tầm nhìn hoặc phương tiện từ 2 kg)",
    "FLIGHT_MGMT_DEVICE": "Thiếu thiết bị quản lý hoạt động bay đã được xác nhận",
    "PERMIT": "Thiếu giấy phép thử nghiệm hoặc phép bay còn hiệu lực",
    "CONFORMANCE": "Chưa đạt đủ CT-1…CT-8 còn hiệu lực với cấu hình hiện tại",
    "INSURANCE": "Thiếu bảo hiểm còn hiệu lực (kiểm lại tại T − 3 ngày làm việc)",
}


@dataclass
class Missing:
    rule: str
    detail: str

    def as_dict(self) -> dict:
        return {"rule": self.rule, "message": RULES[self.rule], "detail": self.detail}


@dataclass
class Eligibility:
    missing: list[Missing] = field(default_factory=list)

    @property
    def eligible(self) -> bool:
        return not self.missing

    def as_dict(self) -> dict:
        return {"eligible": self.eligible, "missing": [m.as_dict() for m in self.missing]}


def _covers(d: Document, start: dt.datetime, end: dt.datetime) -> bool:
    return (d.valid_from is None or d.valid_from <= start) and (d.valid_to is None or d.valid_to >= end)


class RegistryService:
    def __init__(self, sm: sessionmaker[Session]):
        self.sm = sm

    def customer_of_org(self, org_id: uuid.UUID | None) -> Customer | None:
        if org_id is None:
            return None
        with self.sm() as s:
            return s.scalar(select(Customer).where(Customer.org_id == org_id))

    def eligibility(self, aircraft_id: uuid.UUID, pilot_id: uuid.UUID, start: dt.datetime, end: dt.datetime,
                    bvlos: bool, stage: str = "request") -> Eligibility:
        """stage = request (khi nhận yêu cầu, khi xếp lịch) | final (xác nhận lần cuối T − 3: thêm bảo hiểm)."""
        out = Eligibility()
        with self.sm() as s:
            ac, pi = s.get(Aircraft, aircraft_id), s.get(Pilot, pilot_id)
            cu = s.get(Customer, ac.customer_id) if ac else None
            if not ac or not pi or not cu or pi.customer_id != ac.customer_id or cu.status != "active":
                out.missing.append(Missing("OWNERSHIP", "phương tiện/người điều khiển không hợp lệ"))
                return out

            docs = s.scalars(select(Document).where(
                Document.status == "verified",
                or_((Document.owner_type == "aircraft") & (Document.owner_id == ac.id),
                    (Document.owner_type == "pilot") & (Document.owner_id == pi.id),
                    (Document.owner_type == "customer") & (Document.owner_id == cu.id)))).all()

            def has(kinds: tuple[str, ...], owner_types: tuple[str, ...]) -> bool:
                return any(d.kind in kinds and d.owner_type in owner_types and _covers(d, start, end) for d in docs)

            if not has(("registration_certificate", "temporary_registration"), ("aircraft",)):
                out.missing.append(Missing("REGISTRATION", ac.uas_id))
            if ac.in_testing and not has(("technical_status_certificate",), ("aircraft",)):
                out.missing.append(Missing("TECHNICAL_STATUS", ac.uas_id))
            if (bvlos or ac.mtow_kg >= MTOW_LICENCE_KG) and not has(("pilot_licence",), ("pilot",)):
                out.missing.append(Missing("PILOT_LICENCE", pi.full_name))
            if not (ac.flight_mgmt_device_id and ac.flight_mgmt_device_verified):
                out.missing.append(Missing("FLIGHT_MGMT_DEVICE", ac.uas_id))
            if not has(("test_permit", "flight_permit"), ("aircraft", "customer")):
                out.missing.append(Missing("PERMIT", ac.uas_id))

            results = s.scalars(select(ConformanceResult).where(ConformanceResult.aircraft_id == ac.id)
                                .order_by(ConformanceResult.tested_at)).all()
            latest = {r.test: r for r in results}  # lần thử mới nhất của mỗi bài
            bad = [t for t in CT_TESTS if t not in latest or not latest[t].passed or latest[t].valid_until < end
                   or latest[t].config_ref != ac.config_ref]
            if bad:
                out.missing.append(Missing("CONFORMANCE", ", ".join(bad)))
            if stage == "final" and not has(("insurance",), ("aircraft", "customer")):
                out.missing.append(Missing("INSURANCE", cu.name))
        return out
