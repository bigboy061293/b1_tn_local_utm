from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from b1.auth import Principal, require
from b1.identity.roles import Perm

router = APIRouter(prefix="/v1/audit", tags=["Nhật ký truy vết"])


@router.get("")
def list_audit(request: Request, after_seq: int = 0, limit: int = 100, action: str | None = None,
               target_id: str | None = None, p: Principal = Depends(require(Perm.AUDIT_READ))) -> dict:
    rows = request.app.state.audit.list(after_seq, limit, action, target_id)
    return {"items": [{c: getattr(r, c) for c in ("seq", "at", "actor_label", "actor_roles", "action", "outcome", "target_type",
                                                   "target_id", "source_ip", "before", "after", "detail", "prev_hash", "hash")}
                      for r in rows]}


@router.get("/verify")
def verify(request: Request, p: Principal = Depends(require(Perm.AUDIT_READ))) -> dict:
    return request.app.state.audit.verify_chain()
