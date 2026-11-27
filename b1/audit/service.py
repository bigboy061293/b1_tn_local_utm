"""Ghi và kiểm nhật ký truy vết. Đây là giao diện duy nhất của dịch vụ nhật ký cho các dịch vụ khác.

hash = SHA-256(prev_hash ‖ bản ghi chuẩn hoá JSON), bản ghi đầu nối với 64 số 0. Sửa hoặc xoá một bản ghi bất kỳ
làm đứt chuỗi từ bản ghi đó, `verify_chain` chỉ ra số thứ tự đầu tiên bị đứt.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from b1.audit.models import AuditLog

GENESIS = "0" * 64
_LOCK_KEY = 0x0B1A0D17  # khoá tư vấn: ghi nhật ký tuần tự


def _plain(x: Any) -> Any:
    return None if x is None else json.loads(json.dumps(x, default=str, ensure_ascii=False))


def _iso(t: dt.datetime) -> str:
    return t.astimezone(dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def canonical(row: AuditLog) -> bytes:
    body = {
        "seq": row.seq, "at": _iso(row.at), "actor_id": None if row.actor_id is None else str(row.actor_id),
        "actor_label": row.actor_label, "actor_roles": row.actor_roles, "action": row.action, "outcome": row.outcome,
        "target_type": row.target_type, "target_id": row.target_id, "source_ip": row.source_ip,
        "before": row.before, "after": row.after, "detail": row.detail,
    }
    return json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def chain_hash(prev_hash: str, row: AuditLog) -> str:
    return hashlib.sha256(prev_hash.encode() + canonical(row)).hexdigest()


@dataclass(frozen=True)
class Actor:
    id: uuid.UUID | None
    label: str
    roles: tuple[str, ...] = ()
    source_ip: str | None = None


SYSTEM = Actor(None, "system")


class AuditService:
    def __init__(self, sm: sessionmaker[Session]):
        self.sm = sm

    def record(self, actor: Actor, action: str, outcome: str = "ok", *, target_type: str | None = None,
               target_id: Any = None, before: Any = None, after: Any = None, detail: Any = None) -> AuditLog:
        """Ghi một bản ghi trong giao dịch riêng, để lần bị từ chối vẫn được ghi dù thao tác chính không xảy ra."""
        with self.sm() as s, s.begin():
            s.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _LOCK_KEY})
            last = s.execute(select(AuditLog.seq, AuditLog.hash).order_by(AuditLog.seq.desc()).limit(1)).first()
            now = dt.datetime.now(dt.timezone.utc)
            row = AuditLog(
                seq=(last.seq + 1) if last else 1, at=now.replace(microsecond=now.microsecond // 1000 * 1000),
                actor_id=actor.id, actor_label=actor.label, actor_roles=sorted(actor.roles), action=action,
                outcome=outcome, target_type=target_type, target_id=None if target_id is None else str(target_id),
                source_ip=actor.source_ip, before=_plain(before), after=_plain(after), detail=_plain(detail),
                prev_hash=last.hash if last else GENESIS,
            )
            row.hash = chain_hash(row.prev_hash, row)
            s.add(row)
        return row

    def verify_chain(self, batch: int = 5000) -> dict:
        prev, n, after = GENESIS, 0, 0
        with self.sm() as s:
            while True:
                rows = s.scalars(select(AuditLog).where(AuditLog.seq > after).order_by(AuditLog.seq).limit(batch)).all()
                if not rows:
                    return {"ok": True, "records": n, "head": prev}
                for r in rows:
                    n += 1
                    if r.seq != n or r.prev_hash != prev or chain_hash(prev, r) != r.hash:
                        return {"ok": False, "records": n - 1, "first_bad_seq": r.seq}
                    prev = r.hash
                after = rows[-1].seq

    def list(self, after_seq: int = 0, limit: int = 100, action: str | None = None, target_id: str | None = None) -> list[AuditLog]:
        q = select(AuditLog).where(AuditLog.seq > after_seq)
        if action:
            q = q.where(AuditLog.action == action)
        if target_id:
            q = q.where(AuditLog.target_id == target_id)
        with self.sm() as s:
            return list(s.scalars(q.order_by(AuditLog.seq).limit(min(limit, 1000))).all())
