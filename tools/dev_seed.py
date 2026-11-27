"""Dữ liệu thử cho môi trường phát triển cục bộ (không dùng khi vận hành).

Gắn các tài khoản của realm Keycloak phát triển (config/keycloak/realm-b1.json) vào B1:
- kh-demo: khách hàng "Công ty Bay Thử Demo", 2 phương tiện: SIM-A hồ sơ đầy đủ, SIM-B thiếu CT-5 (để xem cảnh chặn
  xếp lịch);
- truong-bp: Trưởng bộ phận; ks-cntt: Kỹ sư CNTT.
Ghi trực tiếp qua lớp dịch vụ với tác nhân "system", có nhật ký truy vết.

    B1_DATABASE_URL=postgresql+psycopg://b1:b1@localhost:5432/b1 python tools/dev_seed.py
"""
from __future__ import annotations

import datetime as dt
import pathlib
import sys
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from b1.audit.service import Actor, AuditService  # noqa: E402
from b1.config import Settings  # noqa: E402
from b1.db import init_db, make_engine, make_sessionmaker  # noqa: E402
from b1.identity.models import Organization, RoleAssignment, UserAccount  # noqa: E402
from b1.registry.models import CT_TESTS, Aircraft, ConformanceResult, Customer, Document, Pilot  # noqa: E402

ISSUER = "http://localhost:8080/realms/b1"
SEED = Actor(None, "system:dev_seed")
UTC = dt.timezone.utc


def main() -> int:
    engine = make_engine(Settings().database_url)
    init_db(engine)
    sm = make_sessionmaker(engine)
    audit = AuditService(sm)
    with sm() as s:
        if s.scalar(select(Organization).where(Organization.name == "Công ty Bay Thử Demo")):
            print("đã có dữ liệu thử")
            return 0
        corridor = Organization(name="Bộ phận quản lý hành lang Tây Ninh", kind="corridor")
        org = Organization(name="Công ty Bay Thử Demo", kind="customer")
        s.add_all([corridor, org])
        s.flush()
        users = [("6f1d1c3e-0000-4000-8000-000000000001", "Khách thử nghiệm", org.id, "customer_operator"),
                 ("6f1d1c3e-0000-4000-8000-000000000002", "Trưởng bộ phận", corridor.id, "corridor_head"),
                 ("6f1d1c3e-0000-4000-8000-000000000003", "Kỹ sư CNTT", corridor.id, "it_engineer")]
        for sub, name, oid, role in users:
            u = UserAccount(issuer=ISSUER, subject=sub, display_name=name, org_id=oid)
            s.add(u)
            s.flush()
            s.add(RoleAssignment(user_id=u.id, role=role))
        cu = Customer(org_id=org.id, name=org.name, tier="B")
        s.add(cu)
        s.flush()
        pilot = Pilot(customer_id=cu.id, full_name="Nguyễn Văn Lái")
        a = Aircraft(customer_id=cu.id, uas_id="1596F3YB1234567890", vn_registration_no="VN-TEST-0001", model="SIM-A VTOL",
                     mtow_kg=25, max_speed_ms=30, flight_mgmt_device_id="QLHD-0001", flight_mgmt_device_verified=True,
                     config_ref="fw-1.0/radio-A")
        b = Aircraft(customer_id=cu.id, uas_id="1596F3YB0000000002", vn_registration_no="VN-TEST-0002", model="SIM-B đa cánh",
                     mtow_kg=6, max_speed_ms=18, flight_mgmt_device_id="QLHD-0002", flight_mgmt_device_verified=True,
                     config_ref="fw-2.3/radio-B")
        s.add_all([pilot, a, b])
        s.flush()
        frm, to = dt.datetime(2026, 1, 1, tzinfo=UTC), dt.datetime(2028, 12, 31, tzinfo=UTC)
        now = dt.datetime.now(UTC)

        def doc(owner_type, owner_id, kind, number):
            s.add(Document(customer_id=cu.id, owner_type=owner_type, owner_id=owner_id, kind=kind, number=number, valid_from=frm,
                           valid_to=to, status="verified", verified_via="manual", verified_at=now, note="dev_seed"))
        for ac in (a, b):
            doc("aircraft", ac.id, "registration_certificate", ac.vn_registration_no)
            doc("aircraft", ac.id, "technical_status_certificate", f"KT-{ac.uas_id[-4:]}")
        doc("customer", cu.id, "test_permit", "GPTN-2026-001")
        doc("customer", cu.id, "insurance", "BH-2026-001")
        tested = dt.datetime(2026, 11, 1, tzinfo=UTC)
        for ac in (a, b):
            for t in CT_TESTS:
                if ac is b and t == "CT-5":
                    continue
                s.add(ConformanceResult(aircraft_id=ac.id, test=t, passed=True, tested_at=tested,
                                        valid_until=tested + dt.timedelta(days=365), config_ref=ac.config_ref))
        doc("pilot", pilot.id, "pilot_licence", "GPDK-001")
        # Một giấy tờ đang chờ xác nhận để cổng khách hàng có ví dụ trạng thái "đã nộp".
        s.add(Document(customer_id=cu.id, owner_type="aircraft", owner_id=b.id, kind="flight_permit", number="PB-2027-014",
                       valid_from=frm, valid_to=to, status="submitted", note="chờ cán bộ xác nhận"))
        s.commit()
        ids = {"customer": str(cu.id), "pilot": str(pilot.id), "aircraft": [str(a.id), str(b.id)]}
    audit.record(SEED, "dev.seed", detail=ids)
    print("đã tạo dữ liệu thử:", ids)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
