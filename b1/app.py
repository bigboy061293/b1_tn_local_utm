"""Ứng dụng B1 (lớp L6). Chạy: `uvicorn --factory b1.app:create_app`."""
from __future__ import annotations

from fastapi import FastAPI

from b1.audit.api import router as audit_router
from b1.audit.service import AuditService
from b1.auth import TokenVerifier
from b1.config import Settings
from b1.db import init_db, make_engine, make_sessionmaker
from b1.identity.api import router as identity_router
from b1.operations.api import router as operations_router
from b1.connectors import i02
from b1.registry.api import router as registry_router
from b1.registry.service import RegistryService

DISCLAIMERS = [
    "B1 không thay thế hệ thống quản lý bay quốc gia.",
    "B1 không phải sản phẩm tin tức hàng không, không thay AIP, NOTAM.",
    "\"Phê duyệt\" trên B1 là phê duyệt lịch bay nội bộ, không phải phép bay.",
    "Hệ thống chỉ nhận biết giao thông hợp tác, không phát hiện phương tiện bay không hợp tác.",
]


def create_app(settings: Settings | None = None, *, create_schema: bool = True) -> FastAPI:
    settings = settings or Settings()
    app = FastAPI(title="B1 Tây Ninh Local UTM", version="0.2.0-dev")
    engine = make_engine(settings.database_url)
    if create_schema:
        init_db(engine)
    app.state.settings = settings
    app.state.engine = engine
    app.state.sessionmaker = make_sessionmaker(engine)
    app.state.audit = AuditService(app.state.sessionmaker)
    app.state.verifier = TokenVerifier(settings)
    app.state.registry = RegistryService(app.state.sessionmaker)
    app.state.i02 = i02.make(settings.i02_connector, settings.i02_mock_file)
    app.include_router(identity_router)
    app.include_router(audit_router)
    app.include_router(registry_router)
    app.include_router(operations_router)

    @app.get("/v1/disclaimers", tags=["Chung"])
    def disclaimers() -> dict:
        return {"items": DISCLAIMERS}

    return app
