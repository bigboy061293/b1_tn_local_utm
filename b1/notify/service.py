from __future__ import annotations

import uuid

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from b1.notify.models import Notification


class NotifyService:
    def __init__(self, sm: sessionmaker[Session]):
        self.sm = sm

    def notify(self, customer_id: uuid.UUID, kind: str, title: str, body: str, *, ref: str | None = None,
               dedup_key: str | None = None) -> bool:
        """Trả True nếu tạo mới; False nếu đã có thông báo cùng dedup_key."""
        with self.sm() as s:
            res = s.execute(insert(Notification).values(
                id=uuid.uuid4(), customer_id=customer_id, kind=kind, ref=ref, title=title[:200], body=body[:2000],
                dedup_key=dedup_key or str(uuid.uuid4())).on_conflict_do_nothing(index_elements=["dedup_key"]))
            s.commit()
            return res.rowcount == 1
