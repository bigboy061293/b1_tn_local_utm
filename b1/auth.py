"""Xác thực OIDC và phân quyền theo vai trò.

B1 là resource server: kiểm chữ ký JWT bằng khoá công khai của bên cấp danh tính (JWKS), kiểm `iss`, `aud`, `exp`.
Vai trò lấy từ CSDL của B1 (không tin vai trò trong token), nên đổi bên cấp danh tính không đổi phân quyền.
Mọi lần bị từ chối đều ghi nhật ký truy vết.
"""
from __future__ import annotations

import datetime as dt
import json
import uuid
from dataclasses import dataclass, field

import jwt
from fastapi import Depends, HTTPException, Request
from sqlalchemy import or_, select

from b1.audit.service import Actor, AuditService
from b1.config import Settings
from b1.identity.models import RoleAssignment, UserAccount
from b1.identity.roles import PRIVILEGED, Perm, Role, allowed


@dataclass(frozen=True)
class Principal:
    user_id: uuid.UUID
    subject: str
    display_name: str
    org_id: uuid.UUID | None
    roles: frozenset[Role]
    mfa: bool
    source_ip: str | None = None
    claims: dict = field(default_factory=dict, compare=False, repr=False)

    def actor(self) -> Actor:
        return Actor(self.user_id, self.subject, tuple(r.value for r in self.roles), self.source_ip)


class TokenVerifier:
    def __init__(self, settings: Settings):
        self.s = settings
        self._jwk_client = jwt.PyJWKClient(settings.jwks_url) if settings.jwks_url else None
        self._file_keys: dict[str, jwt.PyJWK] = {}
        if settings.jwks_file:
            with open(settings.jwks_file, encoding="utf-8") as f:
                for k in json.load(f)["keys"]:
                    self._file_keys[k.get("kid", "")] = jwt.PyJWK(k)

    def verify(self, token: str) -> dict:
        if self._jwk_client:
            key = self._jwk_client.get_signing_key_from_jwt(token).key
        else:
            kid = jwt.get_unverified_header(token).get("kid", "")
            if kid not in self._file_keys:
                raise jwt.InvalidTokenError("khoá ký không có trong JWKS")
            key = self._file_keys[kid].key
        return jwt.decode(token, key, algorithms=["RS256", "ES256", "PS256"], audience=self.s.oidc_audience,
                          issuer=self.s.oidc_issuer, options={"require": ["exp", "iat", "sub", "iss", "aud"]}, leeway=30)


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _deny(request: Request, actor: Actor, action: str, reason: str, status: int, target: dict | None = None) -> HTTPException:
    audit: AuditService = request.app.state.audit
    audit.record(actor, action, "denied", target_type=(target or {}).get("type"), target_id=(target or {}).get("id"),
                 detail={"reason": reason, "method": request.method, "path": request.url.path})
    return HTTPException(status, reason)


def current_principal(request: Request) -> Principal:
    ip = _client_ip(request)
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        raise _deny(request, Actor(None, "anonymous", (), ip), "auth.authenticate", "thiếu token", 401)
    try:
        claims = request.app.state.verifier.verify(auth[7:].strip())
    except jwt.PyJWTError as e:
        raise _deny(request, Actor(None, "anonymous", (), ip), "auth.authenticate", f"token không hợp lệ: {e}", 401)

    now = dt.datetime.now(dt.timezone.utc)
    with request.app.state.sessionmaker() as s:
        user = s.scalar(select(UserAccount).where(UserAccount.issuer == claims["iss"], UserAccount.subject == claims["sub"]))
        label = str(claims["sub"])
        if user is None:
            raise _deny(request, Actor(None, label, (), ip), "auth.authenticate", "tài khoản chưa được cấp trong B1", 403)
        if user.status != "active" or (user.expires_at is not None and user.expires_at <= now):
            raise _deny(request, Actor(user.id, label, (), ip), "auth.authenticate", "tài khoản bị khoá hoặc đã hết hạn", 403)
        rows = s.scalars(select(RoleAssignment.role).where(
            RoleAssignment.user_id == user.id, RoleAssignment.valid_from <= now,
            or_(RoleAssignment.valid_to.is_(None), RoleAssignment.valid_to > now))).all()
    amr = claims.get("amr") or []
    mfa = any(m in request.app.state.settings.mfa_amr_values for m in amr)
    p = Principal(user.id, label, user.display_name, user.org_id, frozenset(Role(r) for r in rows if r in Role._value2member_map_),
                  mfa, ip, claims)
    if request.app.state.settings.require_mfa_all and not p.mfa:
        raise _deny(request, p.actor(), "auth.authenticate", "truy cập phải xác thực đa yếu tố", 403)
    return p


def require(*perms: Perm, target_type: str | None = None, target_param: str | None = None):
    """Dependency FastAPI: cho qua khi người dùng có ít nhất một quyền trong `perms` (quyền đầu tiên khớp được dùng);
    quyền đặc quyền phải có MFA. Mọi lần từ chối ghi nhật ký."""

    def dep(request: Request, p: Principal = Depends(current_principal)) -> Principal:
        target = {"type": target_type, "id": request.path_params.get(target_param)} if target_param else None
        held = [x for x in perms if allowed(p.roles, x)]
        action = "|".join(x.value for x in perms)
        if not held:
            raise _deny(request, p.actor(), action, "vai trò không có quyền này", 403, target)
        if not any(x not in PRIVILEGED or p.mfa for x in held):
            raise _deny(request, p.actor(), action, "thao tác đặc quyền phải xác thực đa yếu tố", 403, target)
        return p

    return dep


def has(p: Principal, perm: Perm) -> bool:
    return allowed(p.roles, perm) and (perm not in PRIVILEGED or p.mfa)
