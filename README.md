# B1 – Tây Ninh Local UTM

Phần mềm đăng ký và quản lý bay tầm thấp cấp tỉnh cho hành lang thử nghiệm phương tiện bay không người lái Tây Ninh.
B1 không cấp phép bay, không điều hành bay, không thay AIP/NOTAM; "phê duyệt" trên B1 là phê duyệt lịch bay nội bộ.

Kế hoạch phiên bản đầu tiên: xem tài liệu kế hoạch của dự án. Repo này đang ở **bước 0 (nền tảng)**.

## Cấu trúc

| Thư mục | Nội dung |
|---|---|
| `config/corridor/` | Dữ liệu cấu hình hành lang GeoJSON (tim tuyến, 4 khối, ràng buộc trần, 5 trạm RIS, lớp tham chiếu hồ Dầu Tiếng). **Sinh bằng `tools/gen_corridor.py`, không sửa tay.** |
| `api/` | Hợp đồng OpenAPI 3.1 bản nháp cho các giao diện (hiện có I-07 telemetry). |
| `docs/` | Mô hình dữ liệu và ghi chú thiết kế. |
| `tests/` | Kiểm thử lược đồ. |

## Chạy kiểm tra

```bash
pip install -r requirements-dev.txt
python tools/gen_corridor.py   # sinh lại GeoJSON, in bảng so độ dài khối với đặc tả
pytest -q tests
```

Hình học theo KML v2.6 **dự thảo**, chưa phải vùng trời đã phê duyệt. Khi có bản KML mới, sửa toạ độ trong `tools/gen_corridor.py` và sinh lại; không sửa mã nghiệp vụ.

## Việc còn mở trong dữ liệu hành lang

- Ranh Bắc khối 4 (rào địa lý cứng) và dải hạn chế 10 km từ biên giới: cần dữ liệu đường biên giới chính thức.
- Phạm vi bảo vệ hồ Dầu Tiếng: hiện chỉ là lớp tham chiếu theo tim tuyến.
- Tầng khai thác từng khối (D3): chưa đưa vào, chờ phép bay.
