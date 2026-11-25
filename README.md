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
| `twin/` | Bản sao số tối thiểu: phương tiện bay mô phỏng (I-07) và 5 trạm RIS ảo (I-06), có tiêm lỗi. |
| `tests/` | Kiểm thử lược đồ và bản sao số. |

## Chạy kiểm tra

```bash
pip install -r requirements-dev.txt
python tools/gen_corridor.py   # sinh lại GeoJSON, in bảng so độ dài khối với đặc tả
pytest -q tests

# bản sao số: 2 phương tiện, 2 phút, lệch 900 m từ giây 30 đến 60, mất C2 từ giây 80
python -m twin.sim --duration 120 --count 2 --inject offset:30:60:900 --inject c2_lost:80:90 > out.jsonl
```

Các lỗi tiêm được: `offset`, `c2_lost`, `low_battery` (phương tiện); `backhaul_cellular`, `station_down`, `ups` (trạm, giá trị là số thứ tự trạm 1–5, tương ứng mức suy giảm L1, L2, L4).

Hình học theo KML v2.6 **dự thảo**, chưa phải vùng trời đã phê duyệt. Khi có bản KML mới, sửa toạ độ trong `tools/gen_corridor.py` và sinh lại; không sửa mã nghiệp vụ.

## Việc còn mở trong dữ liệu hành lang

- Ranh Bắc khối 4 (rào địa lý cứng) và dải hạn chế 10 km từ biên giới: cần dữ liệu đường biên giới chính thức.
- Phạm vi bảo vệ hồ Dầu Tiếng: hiện chỉ là lớp tham chiếu theo tim tuyến.
- Tầng khai thác từng khối (D3): chưa đưa vào, chờ phép bay.
