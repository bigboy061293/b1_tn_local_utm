"""Ứng dụng B1 (lớp L6). Chạy: `uvicorn --factory b1.app:create_app`."""
from __future__ import annotations

import contextlib
import logging
import threading

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from b1.airspace.api import router as airspace_router
from b1.airspace.service import AirspaceService
from b1.audit.api import router as audit_router
from b1.audit.service import AuditService
from b1.auth import TokenVerifier
from b1.config import Settings
from b1.connectors import i02
from b1.db import init_db, make_engine, make_sessionmaker
from b1.gateway.api import router as gateway_router
from b1.identity.api import router as identity_router
from b1.monitoring.api import router as monitoring_router
from b1.monitoring.service import MonitorService
from b1.notify.api import router as notify_router
from b1.notify.service import NotifyService
from b1.operations.api import duty_router
from b1.operations.api import router as operations_router
from b1.operations.service import OperationsService
from b1.portal.routes import STATIC as PORTAL_STATIC
from b1.portal.routes import router as portal_router
from b1.registry.api import router as registry_router
from b1.registry.service import RegistryService
from b1.scheduling.api import router as scheduling_router
from b1.scheduling.service import SchedulingService
from b1.signing import Signer
from b1.surveillance.api import ops_router as surveillance_router
from b1.surveillance.api import router as ris_router
from b1.surveillance.service import SurveillanceService
from b1.telemetry.api import ops_router as telemetry_ops_router
from b1.telemetry.api import router as telemetry_router
from b1.telemetry.ct import CtService
from b1.telemetry.ct_api import router as ct_router
from b1.telemetry.service import TelemetryService

log = logging.getLogger("b1")

DISCLAIMERS = [
    "B1 không thay thế hệ thống quản lý bay quốc gia.",
    "B1 không phải sản phẩm tin tức hàng không, không thay AIP, NOTAM.",
    "\"Phê duyệt\" trên B1 là phê duyệt lịch bay nội bộ, không phải phép bay.",
    "Hệ thống chỉ nhận biết giao thông hợp tác, không phát hiện phương tiện bay không hợp tác.",
]


def create_app(settings: Settings | None = None, *, create_schema: bool = True) -> FastAPI:
    settings = settings or Settings()
    app = FastAPI(title="B1 Tây Ninh Local UTM", version="0.2.0-dev", lifespan=_lifespan)
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
    app.state.operations = OperationsService(app.state.sessionmaker)
    app.state.notify = NotifyService(app.state.sessionmaker)
    app.state.scheduling = SchedulingService(app.state.sessionmaker, app.state.registry, app.state.operations, app.state.audit,
                                             app.state.notify)
    app.state.airspace = AirspaceService(app.state.sessionmaker, app.state.audit)
    if create_schema:
        app.state.airspace.load_config(settings.corridor_dir)
    app.state.signer = Signer(settings.signing_key_file)
    app.state.monitor = MonitorService(app.state.sessionmaker, settings, app.state.audit, app.state.airspace, app.state.operations)
    app.state.surveillance = SurveillanceService(app.state.sessionmaker, settings, app.state.monitor, app.state.airspace)
    app.state.monitor.surveillance = app.state.surveillance
    app.state.telemetry = TelemetryService(app.state.sessionmaker, settings, app.state.operations, app.state.monitor)
    app.state.ctsessions = CtService(app.state.sessionmaker, app.state.registry, app.state.monitor, app.state.audit)
    app.include_router(identity_router)
    app.include_router(audit_router)
    app.include_router(registry_router)
    app.include_router(scheduling_router)
    app.include_router(notify_router)
    app.include_router(gateway_router)
    app.include_router(portal_router)
    app.mount("/portal/static", StaticFiles(directory=PORTAL_STATIC), name="portal-static")
    app.include_router(operations_router)
    app.include_router(duty_router)
    app.include_router(airspace_router)
    app.include_router(telemetry_router)
    app.include_router(telemetry_ops_router)
    app.include_router(ct_router)
    app.include_router(ris_router)
    app.include_router(surveillance_router)
    app.include_router(monitoring_router)

    @app.get("/v1/disclaimers", tags=["Chung"])
    def disclaimers() -> dict:
        return {"items": DISCLAIMERS}

    @app.get("/v1/signing-keys", tags=["Chung"])
    def signing_keys() -> dict:
        """Khoá công khai của B1 (JWKS) để kiểm chữ ký bản ghi cho phép cất cánh."""
        return app.state.signer.jwks()

    return app


@contextlib.asynccontextmanager
async def _lifespan(app: FastAPI):
    """Vòng kiểm định kỳ của giám sát (mất telemetry, trạm, vết cũ) và các kênh MAVLink, MQTT nếu cấu hình."""
    st = app.state.settings
    stop = threading.Event()
    extra = []
    if st.monitor_background:
        def loop():
            while not stop.wait(st.monitor_tick_s):
                try:
                    app.state.monitor.tick()
                except Exception:  # noqa: BLE001
                    log.exception("vòng kiểm giám sát lỗi")
        threading.Thread(target=loop, name="b1-monitor", daemon=True).start()
    if st.mavlink_udp_port:
        from b1.telemetry.mavlink import MavlinkGateway
        app.state.mavlink = MavlinkGateway(app.state.telemetry, app.state.sessionmaker, st.mavlink_udp_host, st.mavlink_udp_port)
        app.state.mavlink.start()
        extra.append(app.state.mavlink)
    if st.mqtt_url:
        from b1.telemetry.mqtt import MqttBridge
        app.state.mqtt = MqttBridge(app.state.telemetry, st.mqtt_url, ca_file=st.mqtt_ca_file, cert_file=st.mqtt_cert_file,
                                    key_file=st.mqtt_key_file)
        app.state.mqtt.start()
        extra.append(app.state.mqtt)
    try:
        yield
    finally:
        stop.set()
        for x in extra:
            x.stop()
