from __future__ import annotations

import datetime as dt
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from b1.auth import Principal, current_principal, require
from b1.db import get_session
from b1.identity.models import Organization, RoleAssignment, UserAccount
from b1.identity.roles import Perm, Role

router = APIRouter(prefix="/v1", tags=["Danh tính"])


class OrgIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    kind: str = Field(pattern="^(corridor|customer|regulator|auditor)$")


class UserIn(BaseModel):
    issuer: str
    subject: str
    display_name: str
    org_id: uuid.UUID | None = None
    expires_at: dt.datetime | None = None
    roles: list[Role] = []


class RoleIn(BaseModel):
    role: Role
    valid_to: dt.datetime | None = None


def _user_out(u: UserAccount, roles: list[RoleAssignment]) -> dict:
    return {"id": str(u.id), "issuer": u.issuer, "subject": u.subject, "display_name": u.display_name,
            "org_id": u.org_id and str(u.org_id), "status": u.status, "expires_at": u.expires_at,
            "roles": [{"id": str(r.id), "role": r.role, "valid_from": r.valid_from, "valid_to": r.valid_to} for r in roles]}


def _active_roles(s: Session, user_id: uuid.UUID) -> list[RoleAssignment]:
    now = dt.datetime.now(dt.timezone.utc)
    return list(s.scalars(select(RoleAssignment).where(
        RoleAssignment.user_id == user_id, or_(RoleAssignment.valid_to.is_(None), RoleAssignment.valid_to > now))).all())


@router.get("/me")
def me(p: Principal = Depends(current_principal)) -> dict:
    return {"id": str(p.user_id), "subject": p.subject, "display_name": p.display_name,
            "roles": sorted(r.value for r in p.roles), "mfa": p.mfa}


@router.post("/organizations", status_code=201)
def create_org(body: OrgIn, request: Request, p: Principal = Depends(require(Perm.USER_MANAGE)),
               s: Session = Depends(get_session)) -> dict:
    o = Organization(name=body.name, kind=body.kind)
    s.add(o)
    s.commit()
    request.app.state.audit.record(p.actor(), "identity.org.create", target_type="organization", target_id=o.id,
                                   after=body.model_dump())
    return {"id": str(o.id), "name": o.name, "kind": o.kind}


@router.post("/users", status_code=201)
def create_user(body: UserIn, request: Request, p: Principal = Depends(require(Perm.USER_MANAGE)),
                s: Session = Depends(get_session)) -> dict:
    if s.scalar(select(UserAccount).where(UserAccount.issuer == body.issuer, UserAccount.subject == body.subject)):
        raise HTTPException(409, "tài khoản đã có")
    u = UserAccount(issuer=body.issuer, subject=body.subject, display_name=body.display_name, org_id=body.org_id,
                    expires_at=body.expires_at)
    s.add(u)
    s.flush()
    roles = [RoleAssignment(user_id=u.id, role=r.value, granted_by=p.user_id) for r in body.roles]
    s.add_all(roles)
    s.commit()
    out = _user_out(u, roles)
    request.app.state.audit.record(p.actor(), "identity.user.create", target_type="user", target_id=u.id, after=out)
    return out


@router.get("/users/{user_id}")
def get_user(user_id: uuid.UUID, p: Principal = Depends(require(Perm.USER_MANAGE)), s: Session = Depends(get_session)) -> dict:
    u = s.get(UserAccount, user_id)
    if not u:
        raise HTTPException(404, "không có tài khoản")
    return _user_out(u, _active_roles(s, u.id))


@router.post("/users/{user_id}/roles", status_code=201)
def grant_role(user_id: uuid.UUID, body: RoleIn, request: Request,
               p: Principal = Depends(require(Perm.USER_MANAGE, target_type="user", target_param="user_id")), s: Session = Depends(get_session)) -> dict:
    u = s.get(UserAccount, user_id)
    if not u:
        raise HTTPException(404, "không có tài khoản")
    before = _user_out(u, _active_roles(s, u.id))
    s.add(RoleAssignment(user_id=u.id, role=body.role.value, valid_to=body.valid_to, granted_by=p.user_id))
    s.commit()
    after = _user_out(u, _active_roles(s, u.id))
    request.app.state.audit.record(p.actor(), "identity.role.grant", target_type="user", target_id=u.id, before=before, after=after)
    return after


@router.delete("/users/{user_id}/roles/{assignment_id}")
def revoke_role(user_id: uuid.UUID, assignment_id: uuid.UUID, request: Request,
                p: Principal = Depends(require(Perm.USER_MANAGE, target_type="user", target_param="user_id")), s: Session = Depends(get_session)) -> dict:
    u = s.get(UserAccount, user_id)
    ra = s.get(RoleAssignment, assignment_id)
    if not u or not ra or ra.user_id != u.id:
        raise HTTPException(404, "không có")
    before = _user_out(u, _active_roles(s, u.id))
    ra.valid_to = dt.datetime.now(dt.timezone.utc)  # không xoá, chỉ kết thúc hiệu lực để còn truy vết
    s.commit()
    after = _user_out(u, _active_roles(s, u.id))
    request.app.state.audit.record(p.actor(), "identity.role.revoke", target_type="user", target_id=u.id, before=before, after=after)
    return after


@router.post("/users/{user_id}/disable")
def disable_user(user_id: uuid.UUID, request: Request,
                 p: Principal = Depends(require(Perm.USER_MANAGE, target_type="user", target_param="user_id")), s: Session = Depends(get_session)) -> dict:
    u = s.get(UserAccount, user_id)
    if not u:
        raise HTTPException(404, "không có tài khoản")
    before = u.status
    u.status = "disabled"
    s.commit()
    request.app.state.audit.record(p.actor(), "identity.user.disable", target_type="user", target_id=u.id,
                                   before={"status": before}, after={"status": u.status})
    return {"id": str(u.id), "status": u.status}
