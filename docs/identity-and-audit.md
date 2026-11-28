# Danh tính, vai trò và nhật ký truy vết (bước 2a)

Theo Đặc tả B1 v0.1: Mục 1.1 (vai trò), F-04 (cho phép cất cánh), F-12 (quản trị người dùng), Mục 6 (xác thực API),
AT-04. Mã: `b1/identity`, `b1/audit`, `b1/auth.py`.

## Đăng nhập

- B1 không tự giữ mật khẩu. Người dùng đăng nhập ở bên cấp danh tính OIDC; B1 chỉ kiểm token JWT (chữ ký theo
  JWKS, `iss`, `aud`, `exp`; thời hạn token ≤1 giờ do bên cấp đặt).
- Bên cấp danh tính đổi bằng cấu hình (`B1_OIDC_ISSUER`, `B1_JWKS_URL`): Keycloak (Apache 2.0) cho giai đoạn đầu,
  sau này I-08 của tỉnh nếu có OIDC.
- **Vai trò do B1 giữ**, không lấy từ token: mỗi tài khoản B1 gắn với đúng một cặp (`iss`, `sub`) và có các vai
  trò có thời hạn. Đổi bên cấp danh tính không đổi phân quyền.
- Tài khoản phải được kỹ sư CNTT cấp trước trong B1; token hợp lệ mà chưa có tài khoản vẫn bị từ chối.
- Tài khoản khách hàng có `expires_at` theo đợt thử; quá hạn thì bị từ chối.
- **Xác thực đa yếu tố:** đọc claim `amr` (RFC 8176). Thao tác đặc quyền (đánh dấu MFA dưới đây) luôn đòi MFA.
  `B1_REQUIRE_MFA_ALL=true` đòi MFA cho mọi truy cập, bật khi B1 mở ra ngoài mạng vận hành.

## Ma trận quyền

| Quyền | Trưởng bộ phận | Cán bộ ATB trực ban | KS vô tuyến, mạng | KS CNTT | KTV bay thử | KH khai thác | KH đào tạo | Cơ quan QL | BP điều phối | Kiểm toán | Cổng tỉnh (máy) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `flight.clear_takeoff` (MFA) | ✓ | ✓ |  |  |  |  |  |  |  |  |  |
| `stop.issue` (MFA) | ✓ | ✓ |  |  |  |  |  |  |  |  |  |
| `schedule.manage` (MFA) | ✓ |  |  |  |  |  |  |  |  |  |  |
| `request.own` |  |  |  |  |  | ✓ | ✓ |  |  |  |  |
| `flight.read_all` | ✓ | ✓ | ✓ | ✓ | ✓ |  |  | ✓ | ✓ | ✓ |  |
| `portal.admin` (MFA) |  |  |  |  |  |  |  |  | ✓ |  |  |
| `user.manage` (MFA) |  |  |  | ✓ |  |  |  |  |  |  |  |
| `audit.read` |  |  |  | ✓ |  |  |  |  |  | ✓ |  |
| `customer.manage` (MFA) | ✓ |  |  |  |  |  |  |  |  |  |  |
| `registry.verify` (MFA) | ✓ |  |  |  |  |  |  |  |  |  |  |
| `conformance.record` (MFA) | ✓ |  |  |  | ✓ |  |  |  |  |  |  |
| `province.submit` |  |  |  |  |  |  |  |  |  |  | ✓ |
| `airspace.read` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |  |
| `airspace.manage` (MFA) | ✓ |  |  |  |  |  |  |  |  |  |  |
| `duty.manage` (MFA) | ✓ |  |  |  |  |  |  |  |  |  |  |

Nguồn: `b1/identity/roles.py`. AT-04 kiểm dòng `flight.clear_takeoff` với mọi vai trò
(`tests/test_at04_takeoff_clearance.py`).

## Nhật ký truy vết

- Ghi: ai (tài khoản, vai trò lúc đó), làm gì, kết quả (`ok`, `denied`, `rejected`), khi nào (UTC, mili giây),
  từ đâu (IP), đối tượng, giá trị trước, giá trị sau.
- **Mọi lần bị từ chối đều được ghi**, kể cả token sai và tài khoản chưa cấp, trong giao dịch riêng nên không mất khi
  thao tác chính không xảy ra.
- **Nối chuỗi mã băm:** `hash = SHA-256(prev_hash ‖ bản ghi chuẩn hoá JSON)`, bản ghi đầu nối với 64 số 0. Ghi tuần tự
  bằng khoá tư vấn của PostgreSQL. `GET /v1/audit/verify` hoặc `python -m b1.admin verify-audit` chỉ ra bản ghi đầu
  tiên bị sửa.
- **Chỉ ghi thêm:** trigger CSDL chặn UPDATE, DELETE, TRUNCATE trên `audit.audit_log`.

## Việc còn mở

1. **"Khi trực"** (đã làm ở bước 3): Trưởng bộ phận chỉ cho phép cất cánh khi có ca trực đang hiệu lực
   (`/v1/duty/shifts`, thông số `B1_DUTY_REQUIRED_ROLES`). Cán bộ an toàn bay trực ban chưa bị đòi ca, vì vai trò đã là trực
   ban; thêm `duty_safety_officer` vào thông số nếu cần siết. Xem `docs/airspace-and-clearance.md`.
2. **Vận hành:** tài khoản CSDL của ứng dụng chỉ được `INSERT, SELECT` trên `audit.audit_log`; chủ sở hữu bảng là tài
   khoản khác. Định kỳ công bố mã băm đầu chuỗi ra ngoài hệ thống (ví dụ in vào báo cáo tháng) để phát hiện cả trường
   hợp ghi lại toàn bộ chuỗi.
3. **Lưu 5 năm:** phân vùng theo năm, chuyển phân vùng cũ sang kho lưu trữ; làm ở bước 6 cùng thời hạn lưu (F-11).
4. **Migration:** lược đồ đang tạo bằng `create_all`; chuyển sang Alembic khi lược đồ bước 2 ổn định.
