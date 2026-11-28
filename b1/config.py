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
