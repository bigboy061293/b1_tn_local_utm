# B1 – Tây Ninh Local UTM

Phần mềm đăng ký và quản lý bay tầm thấp cấp tỉnh cho hành lang thử nghiệm phương tiện bay không người lái Tây Ninh.
B1 không cấp phép bay, không điều hành bay, không thay AIP/NOTAM; "phê duyệt" trên B1 là phê duyệt lịch bay nội bộ.

Kế hoạch phiên bản đầu tiên: xem tài liệu kế hoạch của dự án. Repo này đang ở **bước 2** (danh tính, đăng ký, xếp lịch).

## Cấu trúc

| Thư mục | Nội dung |
|---|---|
| `b1/` | Dịch vụ B1 (lớp L6), Python + FastAPI + PostgreSQL. Mỗi dịch vụ một schema CSDL riêng: `identity`, `audit`, `operations`. |
| `config/corridor/` | Dữ liệu cấu hình hành lang GeoJSON (tim tuyến, 4 khối, ràng buộc trần, 5 trạm RIS, lớp tham chiếu hồ Dầu Tiếng). **Sinh bằng `tools/gen_corridor.py`, không sửa tay.** |
| `api/` | Hợp đồng OpenAPI: bản nháp I-01/I-09, I-06, I-07; `l6-core.openapi.json` xuất từ mã dịch vụ (`tools/export_openapi.py`). |
| `docs/` | Mô hình dữ liệu, danh tính và nhật ký (`identity-and-audit.md`), kết quả thử khái niệm D1. |
| `poc/` | Thử khái niệm bước 1 trên hệ sinh thái InterUSS. |
| `twin/` | Bản sao số tối thiểu: phương tiện bay mô phỏng (I-07) và 5 trạm RIS ảo (I-06), có tiêm lỗi. |
| `tests/` | Kiểm thử lược đồ và bản sao số. |

## Chạy dịch vụ B1 cục bộ

```bash
python tools/dev_token.py --sub it-admin --mfa > /dev/null   # sinh khoá phát triển vào .dev/
docker compose up -d --build                                  # PostGIS + dịch vụ tại http://localhost:8000/docs
docker compose exec api python -m b1.admin bootstrap --issuer https://idp.b1.local/realms/b1 --subject it-admin --name "Quản trị"
curl -H "Authorization: Bearer $(python tools/dev_token.py --sub it-admin --mfa)" localhost:8000/v1/me
```

## Chạy kiểm tra

Các bài của dịch vụ cần PostgreSQL ở localhost:5432 (tài khoản b1/b1), ví dụ `docker compose up -d db`.

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
