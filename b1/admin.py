"""Lệnh quản trị. Tạo tài khoản quản trị đầu tiên (kỹ sư CNTT), có ghi nhật ký với tác nhân "system":

    python -m b1.admin bootstrap --issuer https://idp.b1.local/realms/b1 --subject <sub> --name "Họ tên"
    python -m b1.admin verify-audit
"""
from __future__ import annotations

import argparse
import json

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
    a = ap.parse_args()
    engine = make_engine(Settings().database_url)
    init_db(engine)
    sm = make_sessionmaker(engine)
    audit = AuditService(sm)
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
