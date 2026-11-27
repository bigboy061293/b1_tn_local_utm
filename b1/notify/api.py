from __future__ import annotations

import datetime as dt
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from b1.auth import Principal, require
from b1.db import get_session
from b1.identity.roles import Perm
from b1.notify.models import Notification
from b1.registry.api import dump, my_customer

router = APIRouter(prefix="/v1/notifications", tags=["Thông báo"])


@router.get("")
def list_notifications(request: Request, unread: bool = False, p: Principal = Depends(require(Perm.REQUEST_OWN)),
                       s: Session = Depends(get_session)) -> dict:
    c = my_customer(request, p)
    if c is None:
        return {"items": []}
    q = select(Notification).where(Notification.customer_id == c.id)
    if unread:
        q = q.where(Notification.read_at.is_(None))
    return {"items": [dump(n) for n in s.scalars(q.order_by(Notification.created_at.desc()).limit(200)).all()]}


@router.post("/{notification_id}/read")
def mark_read(notification_id: uuid.UUID, request: Request, p: Principal = Depends(require(Perm.REQUEST_OWN)),
              s: Session = Depends(get_session)) -> dict:
    c = my_customer(request, p)
    n = s.get(Notification, notification_id)
    if n is None or c is None or n.customer_id != c.id:
        raise HTTPException(404, "không có")
    n.read_at = n.read_at or dt.datetime.now(dt.timezone.utc)
    s.commit()
    return dump(n)
