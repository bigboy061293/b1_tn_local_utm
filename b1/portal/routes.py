"""Cổng khách hàng (lớp L7): trang tĩnh, đăng nhập OIDC Authorization Code + PKCE ngay trên trình duyệt, gọi API B1."""
from __future__ import annotations

import pathlib

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, RedirectResponse

STATIC = pathlib.Path(__file__).resolve().parent / "static"
router = APIRouter(include_in_schema=False)


@router.get("/portal")
def portal_redirect() -> RedirectResponse:
    return RedirectResponse("/portal/")


@router.get("/portal/")
def portal_index() -> FileResponse:
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})


@router.get("/ops")
def ops_redirect() -> RedirectResponse:
    return RedirectResponse("/ops/")


@router.get("/ops/")
def ops_index() -> FileResponse:
    """Trang vận hành cho cán bộ: bản đồ trạng thái khối, kế hoạch kích hoạt, gói dữ liệu, hiệp đồng (F-03)."""
    return FileResponse(STATIC / "ops.html", headers={"Cache-Control": "no-store"})


@router.get("/portal/config.json")
def portal_config(request: Request) -> dict:
    s = request.app.state.settings
    return {"issuer": s.oidc_issuer, "client_id": s.portal_client_id, "api": ""}
