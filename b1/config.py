"""Cấu hình B1, đọc từ biến môi trường tiền tố B1_ (ví dụ B1_DATABASE_URL)."""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="B1_")

    database_url: str = "postgresql+psycopg://b1:b1@localhost:5432/b1"

    # Bên cấp danh tính OIDC (Keycloak, hoặc I-08 của tỉnh): đổi bằng cấu hình, không sửa mã.
    oidc_issuer: str = "https://idp.b1.local/realms/b1"
    oidc_audience: str = "b1-api"
    jwks_url: str | None = None   # lấy khoá công khai từ bên cấp danh tính
    jwks_file: str | None = None  # hoặc tệp JWKS cục bộ (phát triển, kiểm thử)

    # Cổng khách hàng (trình duyệt) đăng nhập theo OIDC Authorization Code + PKCE với client công khai này.
    portal_client_id: str = "b1-portal"

    # Xác thực đa yếu tố: bên cấp danh tính ghi phương thức vào claim `amr` (RFC 8176).
    mfa_amr_values: list[str] = ["mfa", "otp", "hwk", "swk"]
    # Mọi truy cập từ xa phải có MFA (Đặc tả Mục 1.1). Bật khi B1 mở ra ngoài mạng vận hành.
    require_mfa_all: bool = False

    area_scope: str = "corridor-tn"

    # Mô-đun kết nối I-02 (đăng ký phương tiện của Bộ Công an): mock | manual.
    i02_connector: str = "mock"
    i02_mock_file: str | None = None

    # Dữ liệu hành lang (GeoJSON sinh bởi tools/gen_corridor.py) nạp vào dịch vụ vùng trời khi khởi động.
    corridor_dir: str = "config/corridor"
    # Khoá ký của B1 (ES256, PEM) cho bản ghi cho phép cất cánh và webhook. Chưa có thì tạo mới (chỉ dùng khi phát triển).
    signing_key_file: str = ".dev/b1_signing_key.pem"
    # Ký điện tử khi cho phép cất cánh: người ký phải đăng nhập lại trong khoảng này (claim auth_time).
    signature_max_auth_age_s: int = 600
    # Chu kỳ luồng sự kiện vùng trời đọc CSDL (giây).
    airspace_poll_s: float = 0.25
    # Vai trò chỉ được cho phép cất cánh khi có ca trực đang hiệu lực (Đặc tả Mục 1.1: Trưởng bộ phận "khi trực").
    duty_required_roles: list[str] = ["corridor_head"]

    # ---------- Bước 4: telemetry, trạm, giám sát tuân thủ ----------
    # Token gửi telemetry phải có phạm vi telemetry:write (I-07).
    telemetry_require_scope: bool = True
    # Mẫu có giờ nguồn sớm hơn giờ B1 quá chừng này (giây) bị loại: đồng hồ nguồn chưa đồng bộ.
    telemetry_max_future_s: float = 5.0
    # Gửi bù (/backfill) tính hạn mức riêng từng chuyến: tốc độ (mẫu/giây) và dung lượng xô.
    # Vượt thì trả 429 kèm Retry-After, để gửi bù hay tải dồn không chiếm đường của mẫu thời gian thực.
    backfill_rate_per_s: float = 1000.0
    backfill_burst: float = 2000.0
    # Hạ cánh: disarmed liên tục chừng này giây (theo giờ nguồn).
    landed_after_disarm_s: float = 10.0
    # Mất telemetry khi đang bay (giây) và chu kỳ kiểm định kỳ.
    telemetry_stale_s: float = 2.0
    monitor_tick_s: float = 0.5
    # Ranh khối, trần đọc lại từ dịch vụ vùng trời sau chừng này giây.
    monitor_context_ttl_s: float = 1.0
    # D6 (demo): nửa bề rộng vùng bình thường và vùng dự phòng tính từ tim tuyến.
    normal_half_width_m: float = 500.0
    contingency_half_width_m: float = 750.0
    battery_warn_pct: float = 25.0
    battery_crit_pct: float = 15.0
    # Độ cao địa hình một giá trị cho cả hành lang và độ cao geoid (EGM) dùng để đổi hệ quy chiếu, chờ mô hình số độ cao.
    terrain_elev_m: float = 25.0
    geoid_undulation_m: float = 0.0
    # Trục thời gian: nội suy khi hai mẫu kẹp cách nhau không quá chừng này (giây).
    timeline_max_interp_gap_s: float = 1.0
    # Gộp vết ADS-B, vết cũ (Đặc tả Mục 4.5).
    adsb_fusion_window_ms: float = 200.0
    track_stale_s: float = 15.0
    track_drop_s: float = 30.0
    # Đối chiếu Remote ID với telemetry.
    rid_mismatch_m: float = 100.0
    rid_match_max_dt_s: float = 1.0
    # Giao thông có người lái gần chuyến đang bay.
    traffic_horizontal_m: float = 2000.0
    traffic_vertical_m: float = 300.0
    # Trạm mất bản tin trạng thái quá chừng này (giây) là mất trạm (L2).
    station_timeout_s: float = 5.0
    # Luồng cảnh báo, bản đồ đọc hộp thư đi mỗi chừng này giây.
    alert_poll_s: float = 0.1
    # Kênh MAVLink v2 UDP và MQTT 5 (để trống thì không mở).
    mavlink_udp_port: int | None = None
    mavlink_udp_host: str = "0.0.0.0"
    mqtt_url: str | None = None
    mqtt_ca_file: str | None = None
    mqtt_cert_file: str | None = None
    mqtt_key_file: str | None = None
    # Chạy vòng kiểm định kỳ trong tiến trình API (tắt khi chạy tiến trình giám sát riêng).
    monitor_background: bool = True
