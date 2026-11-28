"""Lệnh quản trị. Tạo tài khoản quản trị đầu tiên (kỹ sư CNTT), có ghi nhật ký với tác nhân "system":

    python -m b1.admin bootstrap --issuer https://idp.b1.local/realms/b1 --subject <sub> --name "Họ tên"
    python -m b1.admin verify-audit
    B1_ALLOW_TWIN_TOOLS=1 python -m b1.admin twin-flight --uas-id SIM… --block block-1 --start-in-min 10

`twin-flight` chỉ dùng trên bản sao số: tạo một chuyến đã xếp lịch bắt đầu sau vài phút (quy trình xếp lịch thật chỉ nhận
khung từ ngày mai), để chạy cho phép cất cánh và bay ngay. Không chạy được khi thiếu B1_ALLOW_TWIN_TOOLS=1.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import uuid

from sqlalchemy import select

from b1.audit.service import SYSTEM, AuditService
from b1.config import Settings
from b1.db import init_db, make_engine, make_sessionmaker
from b1.identity.models import RoleAssignment, UserAccount
from b1.identity.roles import Role


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("bootstrap")
    b.add_argument("--issuer", required=True)
    b.add_argument("--subject", required=True)
    b.add_argument("--name", required=True)
    sub.add_parser("verify-audit")
    t = sub.add_parser("twin-flight")
    t.add_argument("--uas-id", required=True)
    t.add_argument("--customer-id")
    t.add_argument("--block", action="append", required=True)
    t.add_argument("--start-in-min", type=int, default=10)
    t.add_argument("--minutes", type=int, default=30)
    t.add_argument("--queue-no", default=None)
    a = ap.parse_args()
    engine = make_engine(Settings().database_url)
    init_db(engine)
    sm = make_sessionmaker(engine)
    audit = AuditService(sm)
    if a.cmd == "twin-flight":
        if os.environ.get("B1_ALLOW_TWIN_TOOLS") != "1":
            print("chỉ dùng trên bản sao số: đặt B1_ALLOW_TWIN_TOOLS=1")
            return 2
        from b1.operations.service import OperationsService

        st = dt.datetime.now(dt.timezone.utc).replace(microsecond=0) + dt.timedelta(minutes=a.start_in_min)
        qn = a.queue_no or f"TWIN-{st:%Y%m%d}-{uuid.uuid4().int % 10_000:04d}"
        fid = OperationsService(sm).create_flight(request_ref=qn, uas_id=a.uas_id, customer_id=uuid.UUID(a.customer_id) if a.customer_id
                                                  else None, starts_at=st, ends_at=st + dt.timedelta(minutes=a.minutes),
                                                  block_ids=a.block, bvlos=True)
        audit.record(SYSTEM, "twin.flight.inject", target_type="flight", target_id=fid,
                     detail={"uas_id": a.uas_id, "blocks": a.block, "starts_at": st.isoformat(), "queue_no": qn})
        print(fid)
        return 0
    if a.cmd == "verify-audit":
        r = audit.verify_chain()
        print(json.dumps(r, ensure_ascii=False))
        return 0 if r["ok"] else 1
    with sm() as s:
        if s.scalar(select(UserAccount).where(UserAccount.issuer == a.issuer, UserAccount.subject == a.subject)):
            print("tài khoản đã có")
            return 1
        u = UserAccount(issuer=a.issuer, subject=a.subject, display_name=a.name)
        s.add(u)
        s.flush()
        s.add(RoleAssignment(user_id=u.id, role=Role.IT_ENGINEER.value))
        s.commit()
    audit.record(SYSTEM, "identity.bootstrap", target_type="user", target_id=u.id,
                 after={"subject": a.subject, "display_name": a.name, "roles": [Role.IT_ENGINEER.value]})
    print(u.id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
